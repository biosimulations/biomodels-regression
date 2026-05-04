# ----------------------------
# Document creation (matches your UTC Step demos)
# ----------------------------
from typing import Dict, Any

from biomodels_regression.types import UniformTimeCourseSpec


def make_utc_step_state(
    step_name: str,
    step_address: str,
    sbml_path: str,
    utc: UniformTimeCourseSpec,
) -> Dict[str, Any]:
    return {
        f"{step_name}_step": {
            "_type": "step",
            "address": step_address,
            "config": {
                "model_source": sbml_path,
                "time": float(utc.duration),
                "n_points": int(utc.number_of_points),
            },
            # ✅ paths are a single list, not list-of-lists
            "inputs": {
                "species_concentrations": ["species_concentrations"],
                "species_counts": ["species_counts"],
            },
            "outputs": {
                "result": ["results", step_name],
            },
        },
    }

def make_multi_biomodel_document(
        # biomodel_id: list[str],
        # sbml_path: list[str],
        # utc: UniformTimeCourseSpec,
        # steps: Dict[str, str],
        biomodel_info = list[Dict]
):
    doc = {"state": {}, "schema": {}}
    for biomodel in biomodel_info:
        biomodel_id = biomodel["biomodel_id"]
        sbml_path = biomodel["sbml_path"]
        utc = biomodel["utc"]
        steps = biomodel["steps"]
        single_model_doc = make_biomodel_document(biomodel_id, sbml_path, utc, steps)
        single_model_state = single_model_doc["state"]
        single_model_schema = single_model_doc["schema"]
        doc["state"][biomodel_id] = single_model_state
        doc["schema"][biomodel_id] = single_model_schema
    # TODO add comparison step
    # TODO add emitter
    return doc

def make_biomodel_document(
    biomodel_id: str,
    sbml_path: str,
    utc: UniformTimeCourseSpec,
    steps: Dict[str, str],
) -> Dict[str, Any]:
    """
    Store schemas align with step contracts; numeric_result is assumed to exist already.
    """
    state: Dict[str, Any] = {
        "species_concentrations": {},
        "species_counts": {},
        "results": {},
    }

    schema: Dict[str, Any] = {
        "species_concentrations": "map[float]",
        "species_counts": "map[float]",
        # results[step_name] is numeric_result
        "results": "map[numeric_result]",
    }

    for engine_name, engine_address in steps.items():
        step_key = f"{biomodel_id}_{engine_name}"
        state.update(
            make_utc_step_state(
                step_name=step_key,
                step_address=engine_address,
                sbml_path=sbml_path,
                utc=utc,
            )
        )

    return {"schema": schema, "state": state}
