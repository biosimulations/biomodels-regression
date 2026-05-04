# ----------------------------
# BioModels fetching
# ----------------------------
import os
import tempfile
from pathlib import Path
from typing import Any

import biomodels

from biomodels_regression.biomodel_retrieval.sedml_parsing import read_sedml_doc, extract_first_uniform_time_course, \
    resolve_sbml_source_from_sedml
from biomodels_regression.types import BiomodelLoadResult
from biomodels_regression.utils import _file_name, _iter_entry_files, find_first_sedml, find_first_sbml


def fetch_biomodel_files_to_dir(biomodel_file_entry: Any, out_dir: str) -> str:
    f = biomodels.get_file(biomodel_file_entry)

    if isinstance(f, (str, os.PathLike)) and os.path.exists(str(f)):
        return str(f)

    name = _file_name(biomodel_file_entry)
    out_path = os.path.join(out_dir, name)

    if isinstance(f, bytes):
        Path(out_path).write_bytes(f)
        return out_path

    Path(out_path).write_text(str(f), encoding="utf-8")
    return out_path


def load_biomodel(biomodel_id: str, metadata_or_entry: Any) -> BiomodelLoadResult:
    entry_files = list(_iter_entry_files(metadata_or_entry))

    sedml_entry = find_first_sedml(entry_files)
    sbml_entry = find_first_sbml(entry_files)

    if sedml_entry is None:
        raise ValueError(f"{biomodel_id}: could not find a .sedml file in entry.")
    if sbml_entry is None:
        raise ValueError(f"{biomodel_id}: could not find an SBML (.xml/.sbml) file in entry.")

    with tempfile.TemporaryDirectory(prefix=f"biomodel_{biomodel_id}_") as tmp:
        sedml_path = fetch_biomodel_files_to_dir(sedml_entry, tmp)
        sbml_path = fetch_biomodel_files_to_dir(sbml_entry, tmp)

        sed_doc = read_sedml_doc(sedml_path)
        utc = extract_first_uniform_time_course(sed_doc)

        sedml_dir = os.path.dirname(sedml_path)
        resolved_sbml = resolve_sbml_source_from_sedml(sed_doc, sedml_dir, sbml_path)

        stable_dir = os.path.join(os.getcwd(), "models", biomodel_id)
        os.makedirs(stable_dir, exist_ok=True)

        stable_sedml = os.path.join(stable_dir, os.path.basename(sedml_path))
        stable_sbml = os.path.join(stable_dir, os.path.basename(resolved_sbml))

        Path(stable_sedml).write_bytes(Path(sedml_path).read_bytes())
        Path(stable_sbml).write_bytes(Path(resolved_sbml).read_bytes())

    return BiomodelLoadResult(
        biomodel_id=biomodel_id,
        sbml_path=stable_sbml,
        sedml_path=stable_sedml,
        utc=utc,   # TODO also support steady state
    )

