"""
Load BioModels entries, extract UniformTimeCourse settings from SED-ML,
resolve SBML source, and emit/run process-bigraph documents for UTC steps.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import biomodels
import libsedml

from pbest.globals import get_loaded_core
from process_bigraph import allocate_core, Composite


# ----------------------------
# Runner
# ----------------------------
import zipfile
import pbest
from pbest.utils.input_types import ExecutionProgramArguments
import pbest as pb


async def submit_composite_document(
    document: Dict[str, Any],
    core,
    name: Optional[str] = None,
    outdir: str = "out",
    time: Optional[float] = None,
    save: bool = True,
    max_retries: int = 3,
    retry_delay: float = 5.0,
    sbml_path: Optional[str] = None,
):
    outdir = Path(outdir)
    # Create Omex that gets sent to the server
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    ts_name = f"{name}_{ts}"
    biomodel_pbg = outdir / "models" / ts_name / f"{ts_name}_url"
    omex_file = outdir / f"submit_{ts_name}.omex"
    biomodel_pbg.parent.mkdir(parents=True, exist_ok=True)
    omex_file = str(omex_file)
    # sbml_path = Path(sbml_path).name

    with open(biomodel_pbg, "w") as f:
        json.dump(document, f)

    with zipfile.ZipFile(omex_file, "w") as f:
        f.write(filename=biomodel_pbg, arcname=f"{ts_name}.pbg")
        if sbml_path:
            # sbml_arcname = Path(sbml_path).name
            f.write(filename=sbml_path, arcname=sbml_path)

    # get the runtime
    if time is None:
        times = []
        for node in document["state"].values():
            if isinstance(node, dict) and node.get("_type") == "step":
                t = node.get("config", {}).get("time")
                if isinstance(t, (int, float)):
                    times.append(float(t))
        time = max(times) if times else 10.0

    # -----------------------------------------------------#
    # Specify the input file, time length of experiment,  #
    # and where it gets saved locally                     #
    # -----------------------------------------------------#
    args: ExecutionProgramArguments = ExecutionProgramArguments(
        input_file_path=omex_file,
        interval=time,
        output_directory=outdir
    )

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            # todo -- call http client from here? in multiprocessing

            await pb.run_remote_experiment(prog_args=args)
            print(f"All done executing {name}.")
            return
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                print(f"Attempt {attempt}/{max_retries} failed for {name}: {e}")
                print(f"Retrying in {retry_delay}s ...")
                await asyncio.sleep(retry_delay)
            else:
                print(f"All {max_retries} attempts failed for {name}: {e}")
    raise last_error


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


async def run_biomodels(
        core,
        number_of_models: int = 2
) -> List[BiomodelLoadResult]:
    biomodel_ids = biomodels.get_all_identifiers()[:number_of_models]
    biomodel_metadata = {bid: biomodels.get_metadata(bid) for bid in biomodel_ids}

    # Addresses match your discovered step classes
    steps = {
        "copasi": "local:pbsim_common.simulators.copasi_process.CopasiUTCStep",
        "tellurium": "local:pbsim_common.simulators.tellurium_process.TelluriumUTCStep",
        # "copasi": "local:CopasiUTCStep",
        # "tellurium": "local:TelluriumUTCStep",
    }

    os.makedirs("documents", exist_ok=True)
    loaded: List[BiomodelLoadResult] = []
    failed: List[str] = []

    for biomodel_id in biomodel_ids:
        try:
            meta = biomodel_metadata[biomodel_id]
            result = load_biomodel(biomodel_id, meta)
            loaded.append(result)

            doc = make_biomodel_document(
                biomodel_id=biomodel_id,
                sbml_path=result.sbml_path,
                utc=result.utc,
                steps=steps,
            )

            # Save doc for inspection
            Path(os.path.join("documents", f"{biomodel_id}.json")).write_text(
                json.dumps(doc, indent=2), encoding="utf-8"
            )


            # Run composite
            await submit_composite_document(
                doc,
                core=core,
                name=f"{biomodel_id}_utc",
                outdir="out_biomodels",
                time=None,
                save=True,
                sbml_path=result.sbml_path,
            )
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

    if failed:
        print(f"\n{len(failed)}/{len(biomodel_ids)} model(s) failed: {failed}")

    return loaded

if __name__ == "__main__":
    core = get_loaded_core()
    loaded = asyncio.run(run_biomodels(core, number_of_models=100))
    # loaded = run_biomodels(core, number_of_models=5)
    print(f"Loaded {len(loaded)} biomodel(s).")
