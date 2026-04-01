"""Google Calendar booking service for AI Advisory.

Handles slot generation, availability checking, and booking creation
via the Google Calendar API using a service account with domain-wide delegation.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from config.settings import (
    CALENDAR_ATTENDEES,
    CALENDAR_BUFFER_MINUTES,
    CALENDAR_BUSINESS_END,
    CALENDAR_BUSINESS_START,
    CALENDAR_ID,
    CALENDAR_OWNER_EMAIL,
    CALENDAR_SLOT_DURATION,
    GOOGLE_PRIVATE_KEY,
    GOOGLE_SERVICE_ACCOUNT_EMAIL,
)

logger = logging.getLogger(__name__)

# Business timezone
BUSINESS_TZ = "America/Chicago"


def _get_calendar_client():
    """Build an authenticated Google Calendar API client."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "client_email": GOOGLE_SERVICE_ACCOUNT_EMAIL,
            "private_key": GOOGLE_PRIVATE_KEY,
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        scopes=["https://www.googleapis.com/auth/calendar"],
        subject=CALENDAR_OWNER_EMAIL,
    )
    return build("calendar", "v3", credentials=credentials)


def get_available_slots(days: int = 21) -> dict:
    """Get available booking slots for the next N days.

    Returns:
        Dict with dates (each containing slots) and timezone.
    """
    try:
        import pytz
    except ImportError:
        return _generate_static_slots(days)

    if not GOOGLE_PRIVATE_KEY or GOOGLE_PRIVATE_KEY == "":
        logger.info("No Google Calendar key configured, using static slots")
        return _generate_static_slots(days)

    try:
        service = _get_calendar_client()
    except Exception:
        logger.warning("Google Calendar auth failed, using static slots", exc_info=True)
        return _generate_static_slots(days)

    try:
        result = _fetch_real_slots(service, days)
        if result.get("dates"):
            return result
        logger.info("Google Calendar returned no available slots, using static fallback")
        return _generate_static_slots(days)
    except Exception:
        logger.warning("Google Calendar fetch failed, using static slots", exc_info=True)
        return _generate_static_slots(days)


def _fetch_real_slots(service, days: int) -> dict:
    """Fetch real availability from Google Calendar."""
    import pytz
    tz = pytz.timezone(BUSINESS_TZ)
    now = datetime.now(tz)

    # Min booking lead time: 4 hours from now
    earliest = now + timedelta(hours=4)

    time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
    time_max = time_min + timedelta(days=min(days, 60))

    # Fetch existing events to find busy times
    events_result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=time_min.isoformat(),
        timeMax=time_max.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    busy_ranges = []
    for event in events_result.get("items", []):
        start = event.get("start", {}).get("dateTime")
        end = event.get("end", {}).get("dateTime")
        if start and end:
            busy_ranges.append((
                datetime.fromisoformat(start),
                datetime.fromisoformat(end),
            ))

    # Generate available slots
    dates = []
    current_date = now.date()

    for day_offset in range(days):
        date = current_date + timedelta(days=day_offset)

        # Skip weekends
        if date.weekday() >= 5:
            continue

        day_slots = []
        slot_start = tz.localize(datetime.combine(date, datetime.min.time()).replace(
            hour=CALENDAR_BUSINESS_START, minute=0,
        ))
        day_end = slot_start.replace(hour=CALENDAR_BUSINESS_END)

        while slot_start + timedelta(minutes=CALENDAR_SLOT_DURATION) <= day_end:
            slot_end = slot_start + timedelta(minutes=CALENDAR_SLOT_DURATION)

            # Check if slot is in the future (past earliest booking time)
            if slot_start >= earliest:
                # Check if slot conflicts with any busy range (including buffer)
                buffer = timedelta(minutes=CALENDAR_BUFFER_MINUTES)
                is_free = True
                for busy_start, busy_end in busy_ranges:
                    if slot_start < (busy_end + buffer) and slot_end > (busy_start - buffer):
                        is_free = False
                        break

                if is_free:
                    day_slots.append({
                        "start": slot_start.isoformat(),
                        "end": slot_end.isoformat(),
                    })

            slot_start = slot_end

        if day_slots:
            dates.append({
                "date": date.isoformat(),
                "day_name": date.strftime("%A"),
                "slots": day_slots,
            })

    return {
        "dates": dates,
        "timezone": BUSINESS_TZ,
    }


def create_booking(
    name: str,
    email: str,
    company: str,
    phone: str,
    slot_start: str,
    session_id: str = "",
) -> dict:
    """Create a Google Calendar event with Google Meet link.

    Args:
        name: Attendee name.
        email: Attendee email.
        company: Company name.
        phone: Phone number.
        slot_start: ISO datetime string for the slot start.
        session_id: Advisory session ID for linking.

    Returns:
        Dict with eventId, meetLink, startTime, endTime.
    """
    service = _get_calendar_client()

    start_dt = datetime.fromisoformat(slot_start)
    end_dt = start_dt + timedelta(minutes=CALENDAR_SLOT_DURATION)

    company_label = f" ({company})" if company else ""
    event_body = {
        "summary": f"AI Strategy Call - {name}{company_label}",
        "description": (
            f"AI Architecture Strategy Call\n\n"
            f"Name: {name}\n"
            f"Email: {email}\n"
            f"Company: {company}\n"
            f"Phone: {phone}\n"
            f"Advisory Session: {session_id}\n\n"
            f"Booked via Colaberry AI Workforce Designer"
        ),
        "start": {"dateTime": start_dt.isoformat(), "timeZone": BUSINESS_TZ},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": BUSINESS_TZ},
        "attendees": [
            {"email": email},
        ] + [{"email": e} for e in CALENDAR_ATTENDEES],
        "conferenceData": {
            "createRequest": {
                "requestId": f"advisory-{session_id or 'booking'}-{int(start_dt.timestamp())}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            },
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 60},
                {"method": "popup", "minutes": 15},
            ],
        },
    }

    event = service.events().insert(
        calendarId=CALENDAR_ID,
        body=event_body,
        conferenceDataVersion=1,
        sendUpdates="all",
    ).execute()

    meet_link = ""
    if event.get("conferenceData", {}).get("entryPoints"):
        for ep in event["conferenceData"]["entryPoints"]:
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri", "")
                break

    return {
        "eventId": event.get("id", ""),
        "meetLink": meet_link,
        "startTime": event.get("start", {}).get("dateTime", ""),
        "endTime": event.get("end", {}).get("dateTime", ""),
    }


def _generate_static_slots(days: int = 21) -> dict:
    """Generate static placeholder slots when Google API is unavailable."""
    dates = []
    now = datetime.now(timezone.utc)

    for day_offset in range(1, days + 1):
        date = (now + timedelta(days=day_offset)).date()
        if date.weekday() >= 5:
            continue
        slots = []
        for hour in range(CALENDAR_BUSINESS_START, CALENDAR_BUSINESS_END):
            for minute in [0, 30]:
                start = datetime.combine(date, datetime.min.time().replace(hour=hour, minute=minute))
                end = start + timedelta(minutes=CALENDAR_SLOT_DURATION)
                slots.append({
                    "start": start.isoformat() + "-05:00",
                    "end": end.isoformat() + "-05:00",
                })
        if slots:
            dates.append({"date": date.isoformat(), "day_name": date.strftime("%A"), "slots": slots})

    return {"dates": dates[:15], "timezone": BUSINESS_TZ}
