"""Bulk apply orchestration for planned target changes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.application.inputs import (
    InputResolutionConfig,
    InputResolutionResult,
    PromptFunc,
    has_sensitive_inputs,
    prepare_input_resolution,
    resolve_global_values,
    resolve_target_inputs,
)
from untaped_recipe.application.targets import Target
from untaped_recipe.domain.plan import TargetPlan
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.infrastructure.file_writer import ApplyWriteError as ApplyWriteError
from untaped_recipe.infrastructure.file_writer import flush_changes as flush_changes

__all__ = ["ApplyWriteError", "RunBulkApply", "flush_changes"]

SENSITIVE_DIAGNOSTIC_SUPPRESSED = "diagnostic suppressed for target with sensitive inputs"
SENSITIVE_ERROR_SUPPRESSED = (
    "target planning failed; diagnostic suppressed for target with sensitive inputs"
)


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
        config = prepare_input_resolution(
            recipe,
            fixed_values=inputs,
            input_from=input_from or {},
            interactive=interactive,
            prompt=prompt,
        )
        global_values = resolve_global_values(recipe, config)
        if interactive:
            parallel = 1
        if parallel <= 1 or len(targets) <= 1:
            return [
                self._plan_one(
                    recipe,
                    recipe_dir,
                    local_hook_project,
                    target,
                    config,
                    global_values,
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
                    config,
                    global_values,
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
        global_values: dict[str, object],
    ) -> TargetPlan:
        resolved: InputResolutionResult | None = None
        try:
            resolved = resolve_target_inputs(
                recipe,
                target,
                config=config,
                global_values=global_values,
            )
            plan = self._planner(
                recipe=recipe,
                recipe_dir=recipe_dir,
                local_hook_project=local_hook_project,
                target=target.path,
                inputs=resolved.values,
            )
            return _suppress_sensitive_diagnostics(
                recipe,
                plan.model_copy(
                    update={
                        "display_inputs": resolved.display_values,
                    }
                ),
            )
        except Exception as exc:
            error = str(exc)
            display_inputs = {}
            if resolved is not None:
                display_inputs = resolved.display_values
                if has_sensitive_inputs(recipe.inputs, display_inputs):
                    error = SENSITIVE_ERROR_SUPPRESSED
            return TargetPlan(
                target=target.path,
                status="error",
                error=error,
                display_inputs=display_inputs,
            )


def _suppress_sensitive_diagnostics(
    recipe: Recipe,
    plan: TargetPlan,
) -> TargetPlan:
    if not has_sensitive_inputs(recipe.inputs, plan.display_inputs):
        return plan
    return plan.model_copy(
        update={
            "error": SENSITIVE_ERROR_SUPPRESSED if plan.error else "",
            "warnings": ((SENSITIVE_DIAGNOSTIC_SUPPRESSED,) if plan.warnings else ()),
        }
    )
