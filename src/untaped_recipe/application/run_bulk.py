"""Bulk apply orchestration for planned target changes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.application.inputs import (
    InputResolutionConfig,
    InputResolutionResult,
    PromptFunc,
    resolve_global_inputs,
    resolve_target_inputs,
)
from untaped_recipe.application.targets import Target
from untaped_recipe.domain.plan import TargetPlan
from untaped_recipe.domain.recipe import Recipe
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
        targets: list[Target],
        inputs: dict[str, object],
        input_from: dict[str, str] | None = None,
        interactive: bool = False,
        prompt: PromptFunc | None = None,
        parallel: int = 1,
    ) -> list[TargetPlan]:
        """Return a plan or error row for every target."""
        config = InputResolutionConfig(
            global_values=inputs,
            input_from=input_from or {},
            interactive=interactive,
            prompt=prompt,
        )
        global_inputs = resolve_global_inputs(recipe, config)
        target_values = dict(inputs)
        target_values.update(global_inputs)
        target_config = InputResolutionConfig(
            global_values=target_values,
            input_from=input_from or {},
            interactive=interactive,
            prompt=prompt,
        )
        if interactive:
            parallel = 1
        if parallel <= 1 or len(targets) <= 1:
            return [
                self._plan_one(
                    recipe,
                    recipe_dir,
                    local_hook_project,
                    target,
                    target_config,
                )
                for target in targets
            ]
        indexed_outcomes: list[tuple[int, TargetPlan]] = []
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {
                pool.submit(
                    self._plan_one,
                    recipe,
                    recipe_dir,
                    local_hook_project,
                    target,
                    target_config,
                ): index
                for index, target in enumerate(targets)
            }
            for future in as_completed(futures):
                indexed_outcomes.append((futures[future], future.result()))
        indexed_outcomes.sort(key=lambda item: item[0])
        return [outcome for _, outcome in indexed_outcomes]

    def _plan_one(
        self,
        recipe: Recipe,
        recipe_dir: Path,
        local_hook_project: Path | None,
        target: Target,
        config: InputResolutionConfig,
    ) -> TargetPlan:
        resolved: InputResolutionResult | None = None
        try:
            resolved = resolve_target_inputs(recipe, target, config=config)
            plan = self._planner(
                recipe=recipe,
                recipe_dir=recipe_dir,
                local_hook_project=local_hook_project,
                target=target.path,
                inputs=resolved.values,
            )
            return plan.model_copy(
                update={
                    "inputs": resolved.values,
                    "display_inputs": resolved.display_values,
                }
            )
        except Exception as exc:
            return TargetPlan(
                target=target.path,
                status="error",
                error=str(exc),
                inputs={} if resolved is None else resolved.values,
                display_inputs={} if resolved is None else resolved.display_values,
            )
