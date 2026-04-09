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
# Data structures
# ----------------------------

@dataclass(frozen=True)
class UniformTimeCourseSpec:
    initial_time: float
    output_start_time: float
    output_end_time: float
    number_of_points: int

    @property
    def duration(self) -> float:
        return float(self.output_end_time - self.output_start_time)


@dataclass(frozen=True)
class BiomodelLoadResult:
    biomodel_id: str
    sbml_path: str
    sedml_path: str
    utc: UniformTimeCourseSpec


# ----------------------------
# Helpers: picking files
# ----------------------------

_SBML_RE = re.compile(r"\.(xml|sbml)$", re.IGNORECASE)
_SEDML_RE = re.compile(r"\.sedml$", re.IGNORECASE)


def _iter_entry_files(entry: Any) -> Iterable[Any]:
    if entry is None:
        return []
    if isinstance(entry, (list, tuple)):
        return entry
    if isinstance(entry, dict):
        for key in ("files", "main_files", "model_files"):
            v = entry.get(key)
            if isinstance(v, (list, tuple)):
                return v
        return []
    try:
        return list(entry)
    except TypeError:
        return []


def _file_name(obj: Any) -> str:
    return getattr(obj, "name", str(obj))


def find_first_sedml(entry_files: Iterable[Any]) -> Optional[Any]:
    for f in entry_files:
        if _SEDML_RE.search(_file_name(f)):
            return f
    return None


def find_first_sbml(entry_files: Iterable[Any]) -> Optional[Any]:
    candidates = []
    for f in entry_files:
        name = _file_name(f)
        if _SEDML_RE.search(name):
            continue
        if _SBML_RE.search(name):
            candidates.append(f)

    # Prefer SBML-ish names
    for key in ("sbml", "model"):
        for c in candidates:
            if key in _file_name(c).lower():
                return c

    return candidates[0] if candidates else None


# ----------------------------
# SED-ML parsing
# ----------------------------

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


# ----------------------------
# BioModels fetching
# ----------------------------

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

        stable_dir = os.path.join("models", biomodel_id)
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


# ----------------------------
# Document creation (matches your UTC Step demos)
# ----------------------------

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
