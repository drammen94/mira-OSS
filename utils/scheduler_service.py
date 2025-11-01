
import asyncio
import logging
from datetime import datetime
import concurrent.futures
import inspect
from typing import Dict, List, Callable, Optional, Any
from dataclasses import dataclass

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


@dataclass
class JobRegistration:
    job_id: str
    func: Callable
    trigger: Any
    component: str
    description: str
    replace_existing: bool = True


class SchedulerService:
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone='UTC')
        self._running = False
        self._registered_jobs: Dict[str, JobRegistration] = {}
        
    
    def register_job(
        self,
        job_id: str,
        func: Callable,
        trigger: Any,
        component: str,
        description: str,
        replace_existing: bool = True
    ) -> bool:
        """
        Register a scheduled job.

        Raises:
            RuntimeError: If job registration fails
        """
        try:
            job_reg = JobRegistration(
                job_id=job_id,
                func=func,
                trigger=trigger,
                component=component,
                description=description,
                replace_existing=replace_existing
            )

            self._registered_jobs[job_id] = job_reg

            if self._running:
                self.scheduler.add_job(
                    func=func,
                    trigger=trigger,
                    id=job_id,
                    replace_existing=replace_existing
                )
                logger.info(f"Job {job_id} added to running scheduler for component {component}")
            else:
                logger.info(f"Job {job_id} registered for component {component} (will start when scheduler starts)")

            return True

        except Exception as e:
            logger.error(f"Error registering job {job_id} for component {component}: {e}")
            raise RuntimeError(f"Failed to register job {job_id} for component {component}: {e}") from e
    
    def unregister_job(self, job_id: str) -> bool:
        """
        Unregister a scheduled job.

        Raises:
            RuntimeError: If job unregistration fails
        """
        try:
            if job_id in self._registered_jobs:
                del self._registered_jobs[job_id]

            if self._running:
                self.scheduler.remove_job(job_id)
                logger.info(f"Job {job_id} removed from scheduler")

            return True

        except Exception as e:
            logger.error(f"Error unregistering job {job_id}: {e}")
            raise RuntimeError(f"Failed to unregister job {job_id}: {e}") from e
    
    def start(self):
        """
        Start the scheduler service.

        Raises:
            RuntimeError: If scheduler fails to start or any job fails to add
        """
        if self._running:
            logger.warning("Scheduler service already running")
            return

        try:
            # Add all jobs BEFORE starting the scheduler
            # This prevents APScheduler from recalculating schedules after each job addition
            for job_id, job_reg in self._registered_jobs.items():
                self.scheduler.add_job(
                    func=job_reg.func,
                    trigger=job_reg.trigger,
                    id=job_id,
                    replace_existing=job_reg.replace_existing
                )
                logger.info(f"Added job {job_id} for component {job_reg.component}")

            # Start scheduler once with all jobs registered
            self.scheduler.start()
            self._running = True
            logger.info(f"Scheduler service started with {len(self._registered_jobs)} jobs")

        except Exception as e:
            logger.error(f"Error starting scheduler service: {e}")
            self._running = False
            raise RuntimeError(f"Failed to start scheduler service: {e}") from e
    
    def stop(self):
        if not self._running:
            return

        try:
            logger.info("Stopping scheduler service")

            self.scheduler.shutdown(wait=True)

            self._running = False
            logger.info("Scheduler service stopped")

        except Exception as e:
            logger.error(f"Error stopping scheduler service: {e}")
            self._running = False
    
    def get_service_stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "total_jobs": len(self._registered_jobs),
            "active_jobs": len(self.scheduler.get_jobs()) if self._running else 0,
            "registered_jobs": [
                {
                    "job_id": job_id,
                    "component": job_reg.component,
                    "description": job_reg.description
                }
                for job_id, job_reg in self._registered_jobs.items()
            ]
        }
    
    def get_job_info(self, job_id: str) -> Optional[Dict[str, Any]]:
        if job_id not in self._registered_jobs:
            return None
        
        job_reg = self._registered_jobs[job_id]
        job_info = {
            "job_id": job_id,
            "component": job_reg.component,
            "description": job_reg.description,
            "registered": True,
            "active": False
        }
        
        if self._running:
            try:
                scheduler_job = self.scheduler.get_job(job_id)
                if scheduler_job:
                    job_info["active"] = True
                    job_info["next_run"] = scheduler_job.next_run_time.isoformat() if scheduler_job.next_run_time else None
            except:
                pass
        
        return job_info


scheduler_service = SchedulerService()