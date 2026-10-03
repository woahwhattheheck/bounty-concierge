# SPDX-License-Identifier: MIT
"""Bundle the canonical skill catalog without keeping a second source copy."""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


CATALOG_SOURCE = "data/skill_tags.json"
CATALOG_RESOURCE = "concierge/skill_tags.json"


class BuildPyWithSkillCatalog(build_py):
    """Carry the repository catalog into installed and editable packages."""

    def _catalog_output(self):
        return str(Path(self.build_lib) / CATALOG_RESOURCE)

    def run(self):
        super().run()
        output = self._catalog_output()
        self.mkpath(str(Path(output).parent))
        self.copy_file(CATALOG_SOURCE, output)

    def get_source_files(self):
        sources = super().get_source_files()
        if CATALOG_SOURCE not in sources:
            sources.append(CATALOG_SOURCE)
        return sources

    def get_outputs(self, include_bytecode=1):
        outputs = super().get_outputs(include_bytecode)
        output = self._catalog_output()
        if output not in outputs:
            outputs.append(output)
        return outputs

    def get_output_mapping(self):
        outputs = super().get_output_mapping()
        outputs[self._catalog_output()] = CATALOG_SOURCE
        return outputs


setup(cmdclass={"build_py": BuildPyWithSkillCatalog})
