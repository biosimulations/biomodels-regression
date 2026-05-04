# ----------------------------
# SED-ML parsing
# ----------------------------
import os

import libsedml

from biomodels_regression.types import UniformTimeCourseSpec


def read_sedml_doc(sedml_path: str) -> libsedml.SedDocument:
    doc = libsedml.readSedMLFromFile(str(sedml_path))
    if doc is None:
        raise RuntimeError(f"libsedml returned None reading: {sedml_path}")
    if doc.getNumErrors() > 0:
        msg = doc.getErrorLog().toString()
        raise RuntimeError(f"SED-ML parse errors in {sedml_path}:\n{msg}")
    return doc


def extract_first_uniform_time_course(sed_doc: libsedml.SedDocument) -> UniformTimeCourseSpec:
    n_sims = int(sed_doc.getNumSimulations())
    for i in range(n_sims):
        sim = sed_doc.getSimulation(i)
        if sim is None:
            continue

        is_utc = False
        if hasattr(sim, "isSedUniformTimeCourse"):
            try:
                is_utc = bool(sim.isSedUniformTimeCourse())
            except Exception:
                is_utc = False

        if not is_utc:
            needed = ("getInitialTime", "getOutputStartTime", "getOutputEndTime", "getNumberOfPoints")
            is_utc = all(hasattr(sim, m) for m in needed)

        if not is_utc:
            continue

        return UniformTimeCourseSpec(
            initial_time=float(sim.getInitialTime()),
            output_start_time=float(sim.getOutputStartTime()),
            output_end_time=float(sim.getOutputEndTime()),
            number_of_points=int(sim.getNumberOfPoints()),
        )

    raise ValueError("No UniformTimeCourse simulation found in SED-ML.")


def resolve_sbml_source_from_sedml(
    sed_doc: libsedml.SedDocument,
    sedml_dir: str,
    fallback_sbml_path: str,
) -> str:
    if sed_doc.getNumModels() == 0:
        return fallback_sbml_path

    model = sed_doc.getModel(0)
    if model is None:
        return fallback_sbml_path

    src = model.getSource()
    if not src:
        return fallback_sbml_path

    if src.startswith(("http://", "https://", "urn:", "biomodels:", "BIOMD")):
        return fallback_sbml_path

    candidate = os.path.abspath(os.path.join(sedml_dir, src))
    return candidate if os.path.exists(candidate) else fallback_sbml_path

