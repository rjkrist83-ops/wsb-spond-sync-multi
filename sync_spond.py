import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from spond import spond


LOCAL_TZ = ZoneInfo("Europe/Amsterdam")
TODAY = datetime.now(LOCAL_TZ).date()


TEAMS = [
    {
        "slug": "u12",
        "name": "U12",
        "email": os.environ.get("SPOND_EMAIL_U12", "").strip(),
        "password": os.environ.get("SPOND_PASSWORD_U12", "").strip(),
        "group_id": os.environ.get("SPOND_GROUP_ID_U12", "").strip(),
        "home_address_keywords": ["kaartenmakershoeve 108", "7326 xk"],
        "output_file": "feeds/u12.json",
    },
    {
        "slug": "u15",
        "name": "U15",
        "email": os.environ.get("SPOND_EMAIL_U15", "").strip(),
        "password": os.environ.get("SPOND_PASSWORD_U15", "").strip(),
        "group_id": os.environ.get("SPOND_GROUP_ID_U15", "").strip(),
        "home_address_keywords": ["kaartenmakershoeve 108", "7326 xk"],
        "output_file": "feeds/u15.json",
    },
]


def normalize_location(value):
    if isinstance(value, str):
        return value.strip()

    if isinstance(value, dict):
        feature = (value.get("feature") or "").strip()
        address = (value.get("address") or "").strip()
        locality = (value.get("locality") or "").strip()

        parts = []

        if feature:
            parts.append(feature)

        if address:
            parts.append(address)

        if locality:
            if locality.lower() not in address.lower():
                parts.append(locality)

        deduped = []
        seen = set()

        for part in parts:
            key = part.lower()
            if key not in seen:
                deduped.append(part)
                seen.add(key)

        return ", ".join(deduped)

    return ""


def normalize_start(evt):
    for key in [
        "startDate",
        "start_date",
        "startTime",
        "start_time",
        "start",
        "date",
        "from",
        "startTimestamp",
        "startsAt",
        "startAt",
    ]:
        val = evt.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    return ""


def parse_event_date(value):
    if not value:
        return None

    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    except:
        return None


def to_local_datetime(dt):
    if not dt:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(LOCAL_TZ)


def normalize_event(evt, home_keywords):
    event_id = str(evt.get("id") or evt.get("uid") or "")
    title = (evt.get("heading") or evt.get("name") or "Event").strip()

    raw_start = normalize_start(evt)
    dt = parse_event_date(raw_start)
    local_dt = to_local_datetime(dt)

    location = normalize_location(evt.get("location"))

    event_type = "home" if any(k in location.lower() for k in home_keywords) else "away"

    return {
        "id": event_id,
        "title": title,
        "start": local_dt.strftime("%Y-%m-%dT%H:%M:%S") if local_dt else "",
        "location": location,
        "type": event_type,
    }


def build_member_map(group_detail):
    members = group_detail.get("members", [])
    return {
        str(m.get("id")): m.get("name")
        for m in members if isinstance(m, dict)
    }


def extract_attendance(detail, member_map):
    responses = detail.get("responses", {})
    accepted = responses.get("acceptedIds", [])

    names = []
    for mid in accepted:
        mid = str(mid)
        if mid in member_map:
            names.append(member_map[mid])

    return sorted(set(names))


async def process_team(team):
    print(f"\n=== TEAM {team['name']} ===")

    email = team["email"]
    password = team["password"]
    group_id = team["group_id"]

    if not email or not password or not group_id:
        print("SKIP: incomplete config")
        return

    client = spond.Spond(username=email, password=password)

    try:
        group = await client.get_group(group_id)
        member_map = build_member_map(group)

        events = await client.get_events(group_id)

        output = []

        for evt in events:
            norm = normalize_event(evt, team["home_address_keywords"])

            dt = parse_event_date(norm["start"])
            if not dt:
                continue

            if dt.date() < TODAY:
                continue

            if norm["type"] != "away":
                continue

            detail = await client.get_event(norm["id"])
            attendees = extract_attendance(detail, member_map)

            norm["attending"] = attendees
            output.append(norm)

        output.sort(key=lambda x: x["start"])

        os.makedirs("feeds", exist_ok=True)

        with open(team["output_file"], "w", encoding="utf-8") as f:
            json.dump({
                "team": team["slug"],
                "generated": datetime.now(timezone.utc).isoformat(),
                "events": output
            }, f, indent=2, ensure_ascii=False)

        print(f"OK: {team['output_file']} ({len(output)} events)")

    finally:
        await client.clientsession.close()


async def main():
    for team in TEAMS:
        await process_team(team)


if __name__ == "__main__":
    asyncio.run(main())
