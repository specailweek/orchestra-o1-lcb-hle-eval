"""GAIA runner for Orchestra-o1 MainAgent + SubAgent evaluation."""
from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from base.agent.base_action import BaseAction
from base.engine.async_llm import LLMsConfig, create_llm_instance
from base.engine.logs import logger
from benchmark.common.env import BasicInfo
from benchmark.common.runner import Runner
from benchmark.gaia.scorer import question_scorer
from orchestra_o1.main_agent import MainAgent
from orchestra_o1.prompts.gaia import GAIAMainAgentPrompt
from orchestra_o1.tools.complete import CompleteTool
from orchestra_o1.tools.delegate import DelegateTaskTool


class GAIARunner:
    def __init__(
        self,
        benchmark,
        main_model: str,
        sub_models: List[str],
        max_attempts: int,
        gaia_tools: List[BaseAction],
        task_timeout_seconds: float | None = None,
    ):
        self.benchmark = benchmark
        self.main_model = main_model
        self.sub_models = sub_models
        self.max_attempts = max_attempts
        self.gaia_tools = gaia_tools
        self.task_timeout_seconds = task_timeout_seconds

    def _prepare_csv(self, path: Path | None) -> Tuple[csv.DictWriter | None, asyncio.Lock | None, Any]:
        if path is None:
            return None, None, None
        path.parent.mkdir(parents=True, exist_ok=True)
        append = path.exists() and path.stat().st_size > 0
        handle = path.open("a" if append else "w", newline="", encoding="utf-8")
        fields = [
            "task_id", "level", "main_model", "sub_models", "answer", "expected_answer",
            "success", "reward", "attempts", "sub_cost", "main_cost", "total_cost",
            "timestamp", "start_time", "end_time", "error",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not append:
            writer.writeheader()
        handle.flush()
        return writer, asyncio.Lock(), handle

    @staticmethod
    def _save_trajectory(folder: Path, task_id: str, timestamp: str, payload: Dict[str, Any]) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"gaia_{task_id}_{timestamp}.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        logger.info(f"[GAIA] Trajectory saved to {path}")

    async def run_levels(
        self,
        levels,
        max_concurrency: int,
        csv_path: Path | None,
        trajectory_folder: Path,
        timestamp: str,
    ) -> Dict[str, Dict[str, Any]]:
        semaphore = asyncio.Semaphore(max(1, max_concurrency))
        csv_writer, csv_lock, csv_handle = self._prepare_csv(csv_path)
        results: Dict[str, Dict[str, Any]] = {}

        async def run_single(level_spec):
            task_id = str(level_spec.get("task_id") or level_spec.get("id"))
            level = level_spec.get("Level")
            question = level_spec.get("Question", "")
            expected_answer = level_spec.get("Final answer", "")
            start_time = datetime.now().isoformat()
            env = None
            row: Dict[str, Any]

            async with semaphore:
                try:
                    logger.info(f"[GAIA] Starting task {task_id} (Level {level})")
                    env = self.benchmark.make_env(level_spec, tools=self.gaia_tools)
                    env_info = env.get_basic_info()
                    main_llm = create_llm_instance(LLMsConfig.default().get(self.main_model))
                    alias_to_model = {f"model_{index + 1}": model for index, model in enumerate(self.sub_models)}
                    delegate_tool = DelegateTaskTool(
                        env=env,
                        runner=Runner(),
                        models=self.sub_models,
                        benchmark_type="gaia",
                        alias_to_model=alias_to_model,
                    )
                    main_agent = MainAgent(
                        llm=main_llm,
                        sub_models=self.sub_models,
                        tools=[delegate_tool, CompleteTool()],
                        subagent_tools=self.gaia_tools,
                        prompt_builder=GAIAMainAgentPrompt,
                        max_attempts=self.max_attempts,
                        benchmark_type="gaia",
                        mask_model_names=True,
                    )
                    main_agent.reset(BasicInfo(
                        env_id=task_id,
                        instruction=env_info.instruction,
                        action_space="",
                        max_steps=self.max_attempts,
                        meta_data=env_info.meta_data,
                    ))

                    main_cost_before = main_agent.get_usage_cost()
                    attempts = []
                    final_answer = None
                    task_started = asyncio.get_running_loop().time()
                    for attempt_index in range(self.max_attempts):
                        if self.task_timeout_seconds is None:
                            action, response = await main_agent.step(None, [])
                        else:
                            elapsed = asyncio.get_running_loop().time() - task_started
                            remaining = self.task_timeout_seconds - elapsed
                            if remaining <= 0:
                                raise asyncio.TimeoutError(
                                    f"Task exceeded {self.task_timeout_seconds:g} seconds"
                                )
                            action, response = await asyncio.wait_for(
                                main_agent.step(None, []), timeout=remaining
                            )
                        attempts.append({
                            "attempt": attempt_index + 1,
                            "action": action.get("action"),
                            "params": action.get("params", {}),
                            "result": action.get("result", {}),
                            "raw_response": response,
                        })
                        if action.get("action") == "complete":
                            final_answer = action.get("params", {}).get("answer")
                            break

                    main_cost = max(0.0, main_agent.get_usage_cost() - main_cost_before)
                    sub_cost = sum(float(item.get("result", {}).get("total_cost", 0.0) or 0.0) for item in attempts)
                    reward = question_scorer(final_answer, expected_answer) if final_answer is not None else 0.0
                    success = reward == 1.0
                    end_time = datetime.now().isoformat()
                    payload = {
                        "task_id": task_id,
                        "level": level,
                        "question": question,
                        "expected_answer": expected_answer,
                        "file_name": level_spec.get("file_name", ""),
                        "main_model": self.main_model,
                        "sub_models": self.sub_models,
                        "answer": final_answer,
                        "success": success,
                        "reward": reward,
                        "main_cost": main_cost,
                        "sub_cost": sub_cost,
                        "total_cost": main_cost + sub_cost,
                        "start_time": start_time,
                        "end_time": end_time,
                        "instruction": env_info.instruction,
                        "meta": env_info.meta_data,
                        "trajectory": attempts,
                        "error": None,
                    }
                    self._save_trajectory(trajectory_folder, task_id, timestamp, payload)
                    results[task_id] = {"success": success, "reward": reward, "answer": final_answer, "expected_answer": expected_answer}
                    row = {**payload, "attempts": len(attempts), "timestamp": timestamp, "error": ""}
                except Exception as exc:
                    end_time = datetime.now().isoformat()
                    error_message = str(exc)
                    if isinstance(exc, asyncio.TimeoutError) and not error_message:
                        error_message = f"Task exceeded {self.task_timeout_seconds:g} seconds"
                    logger.error(f"[GAIA] Task {task_id} failed: {type(exc).__name__}: {error_message}")
                    results[task_id] = {"success": False, "reward": 0.0, "error": error_message}
                    row = {
                        "task_id": task_id, "level": level, "main_model": self.main_model,
                        "sub_models": ",".join(self.sub_models), "answer": "", "expected_answer": expected_answer,
                        "success": False, "reward": 0.0, "attempts": 0, "sub_cost": 0.0,
                        "main_cost": 0.0, "total_cost": 0.0, "timestamp": timestamp,
                        "start_time": start_time, "end_time": end_time, "error": error_message,
                    }
                finally:
                    if env is not None:
                        await env.close()

                if csv_writer and csv_lock:
                    selected = {name: row.get(name, "") for name in csv_writer.fieldnames}
                    if isinstance(selected.get("sub_models"), list):
                        selected["sub_models"] = ",".join(selected["sub_models"])
                    async with csv_lock:
                        csv_writer.writerow(selected)
                        csv_handle.flush()

        try:
            await asyncio.gather(*(run_single(level) for level in levels))
        finally:
            if csv_handle:
                csv_handle.close()
        return results
