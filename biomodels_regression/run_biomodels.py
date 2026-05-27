"""
Load BioModels entries, extract UniformTimeCourse settings from SED-ML,
resolve SBML source, and emit/run process-bigraph documents for UTC steps.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pbest.execution.remote.bundle import bundle_and_wait, batch_bundle_and_wait
from pbest.globals import get_loaded_core
from process_bigraph import Composite

from biomodels_regression.biomodel_retrieval.biomodel_fetching import load_biomodel
from biomodels_regression.document_creation import make_biomodel_document
from biomodels_regression.types import BiomodelLoadResult


# ----------------------------
# Runner
# ----------------------------


def run_composite_document(
    document: Dict[str, Any],
    core,
    name: Optional[str] = None,
    outdir: str = "out_biomodels",
    time: Optional[float] = None,
    save: bool = True,
) -> Composite:
    os.makedirs(outdir, exist_ok=True)

    if "state" not in document or not isinstance(document.get("state"), dict):
        document = {"state": document}

    if name is None:
        name = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    if time is None:
        times = []
        for node in document["state"].values():
            if isinstance(node, dict) and node.get("_type") == "step":
                t = node.get("config", {}).get("time")
                if isinstance(t, (int, float)):
                    times.append(float(t))
        time = max(times) if times else 10.0

    sim = Composite(document, core=core)

    if save:
        Path(os.path.join(outdir, f"{name}.json")).write_text(
            json.dumps(document, indent=2), encoding="utf-8"
        )
        Path(os.path.join(outdir, f"{name}_schema.json")).write_text(
            json.dumps(core.render(sim.schema), indent=2), encoding="utf-8"
        )

    print(f"⏱ Running {name} for {time}s ...")
    sim.run(time)
    print(f"✅ Done: {name}")

    if save:
        try:
            serialized = core.serialize(sim.schema, sim.state)
            Path(os.path.join(outdir, f"{name}_state.json")).write_text(
                json.dumps(serialized, indent=2), encoding="utf-8"
            )
        except Exception as e:
            print(f"⚠ Could not serialize final state: {e}")

    return sim


from pbest.execution.remote.batch import batch_run_remote_experiment_and_wait
from pbest.utils.input_types import ExperimentSubmission
import biomodels


async def run_biomodels(
        core,
        number_of_models: int = 2,
        working_dir: Optional[Path] = None,
) -> List[BiomodelLoadResult]:
    biomodel_ids = biomodels.get_all_identifiers()
    biomodel_metadata = {}
    for biomodel_id in biomodel_ids:
        try:
            biomodel_metadata[biomodel_id] = biomodels.get_metadata(biomodel_id)
        except Exception as e:
            print(f"Can't decode biomodel_id: {biomodel_id}. {e}")

    # Addresses match your discovered step classes
    steps = {
        "copasi": "local:pbsim_common.simulators.copasi_process.CopasiUTCStep",
        "tellurium": "local:pbsim_common.simulators.tellurium_process.TelluriumUTCStep",
        # "copasi": "local:CopasiUTCStep",
        # "tellurium": "local:TelluriumUTCStep",
    }

    if working_dir is None:
        working_dir = os.getcwd()

    os.makedirs(os.path.join(working_dir, "documents"), exist_ok=True)
    loaded: List[BiomodelLoadResult] = []
    failed: List[str] = []
    submissions: list[ExperimentSubmission] = []

    for biomodel_id in biomodel_ids:
        try:
            meta = biomodel_metadata[biomodel_id]
            result = load_biomodel(biomodel_id, meta, working_dir)
            loaded.append(result)

            doc = make_biomodel_document(
                biomodel_id=biomodel_id,
                sbml_path=result.sbml_path,
                utc=result.utc,
                steps=steps,
            )

            # Save doc for inspection
            Path(os.path.join(working_dir, "documents", f"{biomodel_id}.json")).write_text(
                json.dumps(doc, indent=2), encoding="utf-8"
            )

            times = []
            for node in doc["state"].values():
                if isinstance(node, dict) and node.get("_type") == "step":
                    t = node.get("config", {}).get("time")
                    if isinstance(t, (int, float)):
                        times.append(float(t))
            time = max(times) if times else 10.0
            time = 1000 if time > 1000 else time

            submissions.append(ExperimentSubmission(pbg=doc, interval=time))

            # run_composite_document(
            #     doc,
            #     core=core,
            #     name=f"{biomodel_id}_utc",
            #     outdir="out_biomodels",
            #     time=None,
            #     save=True,
            # )
        except Exception as e:
            print(f"FAILED {biomodel_id}: {e}")
            failed.append(biomodel_id)

    output_dir = Path(os.path.join(working_dir, "results"))
    os.makedirs(output_dir, exist_ok=True)
    await batch_bundle_and_wait(submissions=submissions, output_dir=output_dir, bundle_size=100)

    # if failed:
    #     print(f"\n{len(failed)}/{len(biomodel_ids)} model(s) failed: {failed}")
    #
    # return loaded

if __name__ == "__main__":
    core = get_loaded_core()
    top_dir = Path(os.getcwd()).parent / "regression_run"
    os.makedirs(top_dir, exist_ok=True)
    start = time.time()
    loaded = asyncio.run(run_biomodels(core, number_of_models=1000, working_dir=top_dir))
    print(f"Ran for {time.time() - start} seconds")
