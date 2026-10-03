# SPDX-License-Identifier: MIT
"""Bundle the canonical skill catalog and FAQ docs from their source files."""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


CATALOG_SOURCE = "data/skill_tags.json"
CATALOG_RESOURCE = "concierge/skill_tags.json"


class BuildPyWithSkillCatalog(build_py):
    """Carry repository resources into installed and editable packages."""

    def _catalog_output(self):
        return str(Path(self.build_lib) / CATALOG_RESOURCE)

    def _docs_outputs(self):
        destination = Path(self.build_lib) / "concierge" / "docs"
        return {
            str(destination / source.name): str(source)
            for source in sorted(Path("docs").glob("*.md"))
            if source.is_file()
        }

    def run(self):
        super().run()
        output = self._catalog_output()
        self.mkpath(str(Path(output).parent))
        self.copy_file(CATALOG_SOURCE, output)
        for output, source in self._docs_outputs().items():
            self.mkpath(str(Path(output).parent))
            self.copy_file(source, output)

    def get_source_files(self):
        sources = super().get_source_files()
        if CATALOG_SOURCE not in sources:
            sources.append(CATALOG_SOURCE)
        sources.extend(source for source in self._docs_outputs().values()
                       if source not in sources)
        return sources

    def get_outputs(self, include_bytecode=1):
        outputs = super().get_outputs(include_bytecode)
        output = self._catalog_output()
        if output not in outputs:
            outputs.append(output)
        outputs.extend(output for output in self._docs_outputs()
                       if output not in outputs)
        return outputs

    def get_output_mapping(self):
        outputs = super().get_output_mapping()
        outputs[self._catalog_output()] = CATALOG_SOURCE
        outputs.update(self._docs_outputs())
        return outputs


setup(cmdclass={"build_py": BuildPyWithSkillCatalog})
