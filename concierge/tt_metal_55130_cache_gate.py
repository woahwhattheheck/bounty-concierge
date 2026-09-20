#!/usr/bin/env python3
"""Acceptance gate for tt-metal #55130 bias_gelu mode/cache isolation.

This gate is intentionally source-level. It does not claim device execution.
It checks that both classic and Quasar binary_ng trees:
1. represent bias_gelu's compile-time mode as structured op parameters;
2. include those parameters in the program-cache key only for BIAS_GELU;
3. populate the parameters for both tensor/tensor and tensor/scalar paths;
4. feed them into code generation as a parametrized GELU postprocess; and
5. carry a regression that exercises one cache with default/false/true modes.
"""

from __future__ import print_function

import argparse
import ast
import json
import re
from pathlib import Path

TREE_SPECS = {
    "classic": "ttnn/cpp/ttnn/operations/eltwise",
    "quasar": "ttnn/cpp/ttnn/operations/experimental/quasar",
}

TEST_PATH = "tests/ttnn/unit_tests/operations/eltwise/test_binary_fp32.py"


def _strip_cpp_comments(text):
    """Remove C/C++ comments while preserving quoted strings."""
    out = []
    i = 0
    state = "code"
    quote = None
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if ch in ('"', "'"):
                quote = ch
                state = "string"
                out.append(ch)
                i += 1
            elif ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
            elif ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
            else:
                out.append(ch)
                i += 1
        elif state == "string":
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
            elif ch == quote:
                state = "code"
                quote = None
                i += 1
            else:
                i += 1
        elif state == "line_comment":
            if ch == "\n":
                out.append("\n")
                state = "code"
            i += 1
        else:
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
            else:
                i += 1
    return "".join(out)


def _read(root, relative_path):
    path = Path(root) / relative_path
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _normalized(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _tree_report(root, name, prefix):
    rel = {
        "params": "%s/binary/common/binary_op_types.hpp" % prefix,
        "attrs": "%s/binary_ng/device/binary_ng_device_operation.hpp" % prefix,
        "dispatch": "%s/binary_ng/device/binary_ng_device_operation.cpp" % prefix,
        "op_config": "%s/binary_ng/device/binary_ng_utils.cpp" % prefix,
        "factory": "%s/binary_ng/device/binary_ng_program_factory.cpp" % prefix,
    }
    raw = {key: _read(root, path) for key, path in rel.items()}
    missing = [rel[key] for key, value in raw.items() if value is None]
    if missing:
        return {"name": name, "pass": False, "missing_files": missing, "checks": {}}

    code = {key: _strip_cpp_comments(value) for key, value in raw.items()}
    norm = {key: _normalized(value) for key, value in code.items()}

    checks = {}
    checks["bias_params_default_false"] = bool(
        re.search(
            r"struct\s+BiasGeluParams\s*\{[^}]*bool\s+fast_and_approximate\s*=\s*false\s*;",
            code["params"],
            flags=re.S,
        )
    )
    checks["params_variant_contains_bias_gelu"] = bool(
        re.search(
            r"using\s+BinaryOpParams\s*=\s*std::variant\s*<[^>]*BiasGeluParams[^>]*>\s*;",
            code["params"],
            flags=re.S,
        )
    )
    checks["cache_attribute_named"] = '"op_params"' in code["attrs"]
    checks["cache_value_bias_gelu_scoped"] = bool(
        re.search(
            r"binary_op_type\s*==\s*BinaryOpType::BIAS_GELU\s*\?\s*op_params\s*:\s*"
            r"std::optional\s*<\s*binary::BinaryOpParams\s*>\s*\{\s*\}",
            code["attrs"],
            flags=re.S,
        )
    )

    population_pattern = re.compile(
        r"BiasGeluParams\s*\{\s*\.fast_and_approximate\s*=\s*"
        r"fast_and_approximate_mode\.value_or\s*\(\s*false\s*\)\s*\}",
        flags=re.S,
    )
    population_count = len(population_pattern.findall(code["dispatch"]))
    checks["tensor_and_scalar_populate_mode"] = population_count >= 2

    checks["op_config_consumes_params"] = (
        "operation_attributes.op_params" in norm["factory"]
        and bool(re.search(r"\bOpConfig\s*\(", code["factory"]))
        and bool(re.search(r"\bop_params\b", code["factory"]))
    )
    checks["gelu_codegen_is_parametrized"] = bool(
        re.search(
            r"case\s+BinaryOpType::BIAS_GELU\s*:\s*\{.*?"
            r"std::get_if\s*<\s*binary::BiasGeluParams\s*>.*?"
            r"EltwiseUnaryWithParam\s*\{[^}]*UnaryOpType::GELU[^}]*"
            r"fast_and_approximate\s*\?\s*1\.0f\s*:\s*0\.0f[^}]*\}",
            code["op_config"],
            flags=re.S,
        )
    )

    return {
        "name": name,
        "pass": all(checks.values()),
        "missing_files": [],
        "mode_population_count": population_count,
        "checks": checks,
    }


def _call_mode(call):
    for keyword in call.keywords:
        if keyword.arg != "fast_and_approximate_mode":
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and value.value is True:
            return "true"
        if isinstance(value, ast.Constant) and value.value is False:
            return "false"
        if isinstance(value, getattr(ast, "NameConstant", ast.Constant)):
            if getattr(value, "value", None) is True:
                return "true"
            if getattr(value, "value", None) is False:
                return "false"
        return "dynamic"
    return "default"


def _is_bias_gelu_call(call):
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr == "bias_gelu"


def _is_method_call(call, method):
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr == method


def _cache_regression_report(root):
    text = _read(root, TEST_PATH)
    if text is None:
        return {"pass": False, "path": TEST_PATH, "reason": "missing test file", "candidate_tests": []}
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return {
            "pass": False,
            "path": TEST_PATH,
            "reason": "test file does not parse: %s" % exc,
            "candidate_tests": [],
        }

    candidates = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [sub for sub in ast.walk(node) if isinstance(sub, ast.Call)]
        modes = {_call_mode(call) for call in calls if _is_bias_gelu_call(call)}
        has_enable = any(_is_method_call(call, "enable_program_cache") for call in calls)
        has_count = any(_is_method_call(call, "num_program_cache_entries") for call in calls)
        has_clear = any(
            _is_method_call(call, "clear_program_cache")
            or _is_method_call(call, "disable_and_clear_program_cache")
            for call in calls
        )
        if modes or has_enable or has_count:
            candidates.append(
                {
                    "name": node.name,
                    "modes": sorted(modes),
                    "enable_program_cache": has_enable,
                    "count_program_cache_entries": has_count,
                    "clears_program_cache": has_clear,
                }
            )
        if {"default", "false", "true"}.issubset(modes) and has_enable and has_count and has_clear:
            return {
                "pass": True,
                "path": TEST_PATH,
                "reason": "cache-isolation regression exercises default/false/true in one cache lifetime",
                "test": node.name,
                "candidate_tests": candidates,
            }

    return {
        "pass": False,
        "path": TEST_PATH,
        "reason": "no test exercises default/false/true bias_gelu modes with cache enable/count/clear",
        "candidate_tests": candidates,
    }


def evaluate(root):
    root = Path(root)
    trees = [_tree_report(root, name, prefix) for name, prefix in TREE_SPECS.items()]
    cache_test = _cache_regression_report(root)
    classic = trees[0]["checks"]
    quasar = trees[1]["checks"]
    shared = sorted(set(classic) | set(quasar))
    symmetry = all(classic.get(key) == quasar.get(key) for key in shared)
    passed = all(item["pass"] for item in trees) and cache_test["pass"] and symmetry
    return {
        "schema": "tt-metal-55130-cache-mode-gate/v1",
        "verdict": "PASS" if passed else "HOLD",
        "root": str(root),
        "trees": trees,
        "classic_quasar_check_symmetry": symmetry,
        "program_cache_regression": cache_test,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="tt-metal checkout root")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args(argv)
    report = evaluate(args.root)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print("%s: %s" % (report["schema"], report["verdict"]))
        for tree in report["trees"]:
            print("- %s: %s" % (tree["name"], "PASS" if tree["pass"] else "FAIL"))
            for key, value in sorted(tree["checks"].items()):
                print("    %s: %s" % (key, "PASS" if value else "FAIL"))
        print(
            "- program_cache_regression: %s (%s)"
            % (
                "PASS" if report["program_cache_regression"]["pass"] else "FAIL",
                report["program_cache_regression"]["reason"],
            )
        )
    return 0 if report["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
