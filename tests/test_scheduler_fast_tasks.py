"""Regression: a task that completes before the loop waits again must still be processed."""
import asyncio

from healthcare_rag.orch.branch import ProcessingBranch
from healthcare_rag.orch.scheduler import get_active_branch_tasks, launch_task


def test_done_but_unprocessed_tasks_are_still_active():
    async def main():
        branch = ProcessingBranch("q", "initial")
        active = {}

        async def instant():
            return "done"

        task = launch_task(branch, "validate", instant(), active)
        await asyncio.sleep(0)  # let it finish
        assert task.done()
        # Still tracked until its result is processed
        assert task in get_active_branch_tasks(active, {branch.branch_id: branch})

    asyncio.run(main())
