"""Bulk apply orchestration for planned target changes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.domain.plan import TargetPlan
from untaped_recipe.domain.recipe import InputSpec, Recipe
from untaped_recipe.infrastructure.file_writer import ApplyWriteError as ApplyWriteError
from untaped_recipe.infrastructure.file_writer import flush_changes as flush_changes

__all__ = ["ApplyWriteError", "RunBulkApply", "flush_changes"]


class RunBulkApply:
    """Plan a recipe across many target directories."""

    def __init__(self, planner: ApplyRecipe) -> None:
        self._planner = planner

    def plan(
        self,
        *,
        recipe: Recipe,
        recipe_dir: Path,
        local_hook_project: Path | None,
        targets: list[Path],
        inputs: dict[str, object],
        parallel: int = 1,
    ) -> list[TargetPlan]:
        """Return a plan or error row for every target."""
        resolved_inputs = InputSpec.resolve_all(recipe.inputs, overrides=inputs)
        if parallel <= 1 or len(targets) <= 1:
            return [
                self._plan_one(
                    recipe,
                    recipe_dir,
                    local_hook_project,
                    target,
                    resolved_inputs,
                )
                for target in targets
            ]
        outcomes: list[TargetPlan] = []
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {
                pool.submit(
                    self._plan_one,
                    recipe,
                    recipe_dir,
                    local_hook_project,
                    target,
                    resolved_inputs,
                ): index
                for index, target in enumerate(targets)
            }
            for future in as_completed(futures):
                outcomes.append(future.result())
        order = {target: index for index, target in enumerate(targets)}
        outcomes.sort(key=lambda plan: order.get(plan.target, len(order)))
        return outcomes

    def _plan_one(
        self,
        recipe: Recipe,
        recipe_dir: Path,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
    ) -> TargetPlan:
        try:
            return self._planner(
                recipe=recipe,
                recipe_dir=recipe_dir,
                local_hook_project=local_hook_project,
                target=target,
                inputs=inputs,
            )
        except Exception as exc:
            return TargetPlan(target=target, status="error", error=str(exc))
