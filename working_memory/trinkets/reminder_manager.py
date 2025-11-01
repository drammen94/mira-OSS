"""Reminder manager trinket for system prompt injection."""
import logging
import datetime
from typing import Dict, Any, List

from utils.timezone_utils import convert_from_utc, format_datetime
from utils.user_context import get_user_timezone
from .base import EventAwareTrinket

logger = logging.getLogger(__name__)


class ReminderManager(EventAwareTrinket):
    """
    Manages reminder information for the system prompt.
    
    Fetches active reminders from the reminder tool when requested.
    """
    
    def _get_variable_name(self) -> str:
        """Reminder manager publishes to 'active_reminders'."""
        return "active_reminders"
    
    def generate_content(self, context: Dict[str, Any]) -> str:
        """
        Generate reminder content by fetching from reminder tool.

        Args:
            context: Update context (unused for reminder manager)

        Returns:
            Formatted reminders section or empty string if no reminders

        Raises:
            Exception: If ReminderTool operations fail (infrastructure/filesystem issues)
        """
        from tools.implementations.reminder_tool import ReminderTool
        reminder_tool = ReminderTool()

        # Let infrastructure failures propagate
        overdue_result = reminder_tool.run(
            operation="get_reminders",
            date_type="overdue",
            category="user"
        )

        today_result = reminder_tool.run(
            operation="get_reminders",
            date_type="today",
            category="user"
        )

        # Get internal reminders separately
        internal_overdue = reminder_tool.run(
            operation="get_reminders",
            date_type="overdue",
            category="internal"
        )

        internal_today = reminder_tool.run(
            operation="get_reminders",
            date_type="today",
            category="internal"
        )

        # Collect and format reminders
        user_reminders = self._collect_reminders([overdue_result, today_result])
        internal_reminders = self._collect_reminders([internal_overdue, internal_today])

        if not user_reminders and not internal_reminders:
            logger.debug("No active reminders")
            return ""  # Legitimately empty - user has no reminders set

        # Format reminder content
        reminder_info = self._format_reminders(user_reminders, internal_reminders)
        logger.debug(f"Generated reminder info with {len(user_reminders)} user and {len(internal_reminders)} internal reminders")
        return reminder_info
    
    def _collect_reminders(self, results: List[Dict]) -> List[Dict]:
        """Collect non-completed reminders from multiple results."""
        reminders = []
        for result in results:
            if result.get("count", 0) > 0:
                for reminder in result.get("reminders", []):
                    if not reminder.get('completed', False):
                        reminders.append(reminder)
        return reminders
    
    def _format_reminders(self, user_reminders: List[Dict], internal_reminders: List[Dict]) -> str:
        """Format reminders into a structured section."""
        user_tz = get_user_timezone()
        
        reminder_info = "# Active Reminders\n"
        
        # Display user reminders
        if user_reminders:
            reminder_info += "The user has the following reminders:\n\n"
            for reminder in user_reminders:
                # Convert UTC reminder time to user's timezone
                date_obj = datetime.datetime.fromisoformat(reminder["reminder_date"])
                local_time = convert_from_utc(date_obj, user_tz)
                formatted_time = format_datetime(local_time, 'date_time')
                reminder_info += f"* {reminder['encrypted__title']} - {formatted_time}\n"
                if reminder.get('encrypted__description'):
                    reminder_info += f"  Details: {reminder['encrypted__description']}\n"
            reminder_info += "\nPlease remind the user about these during the continuum if relevant.\n"
        
        # Display internal reminders in a separate section
        if internal_reminders:
            if user_reminders:
                reminder_info += "\n---\n"
            reminder_info += "\n## Internal Reminders (MIRA's notes)\n"
            reminder_info += "These are internal reminders for MIRA to track:\n\n"
            for reminder in internal_reminders:
                # Convert UTC reminder time to user's timezone
                date_obj = datetime.datetime.fromisoformat(reminder["reminder_date"])
                local_time = convert_from_utc(date_obj, user_tz)
                formatted_time = format_datetime(local_time, 'date_time')
                reminder_info += f"* {reminder['encrypted__title']} - {formatted_time}\n"
                if reminder.get('encrypted__description'):
                    reminder_info += f"  Details: {reminder['encrypted__description']}\n"
        
        return reminder_info.rstrip()