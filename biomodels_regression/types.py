# ----------------------------
# Data structures
# ----------------------------
from dataclasses import dataclass


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

