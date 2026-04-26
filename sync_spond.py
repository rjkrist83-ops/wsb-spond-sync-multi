import asyncio
import json
import os
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
    {
        "slug": "vs",
        "name": "VS",
        "email": os.environ.get("SPOND_EMAIL_U15", "").strip(),
        "password": os.environ.get("SPOND_PASSWORD_U15", "").strip(),
        "group_id": os.environ.get("SPOND_GROUP_ID_VS", "").strip(),
        "home_address_keywords": [
            "kaartenmakershoeve 108",
            "7326 xk",
            "colmschaterstraatweg 3",
            "7433 pr",
            "schalkhaar",
        ],
        "output_file": "feeds/vs.json",
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

        if locality and locality.lower() not in address.lower():
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
    except Exception:
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
    location_lc = location.lower()
    matched_keywords = [k for k in home_keywords if k in location_lc]
    event_type = "home" if matched_keywords else "away"

    return {
        "id": event_id,
        "title": title,
        "raw_start": raw_start,
        "start": local_dt.strftime("%Y-%m-%dT%H:%M:%S") if local_dt else "",
        "location": location,
        "type": event_type,
        "matched_home_keywords": matched_keywords,
    }


def member_name(member):
    if not isinstance(member, dict):
        return ""

    for key in ["name", "displayName", "display_name", "fullName", "full_name"]:
        value = member.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().split(" ")[0]

    first = (
        member.get("firstName")
        or member.get("first_name")
        or member.get("firstname")
        or ""
    )

    if first:
        return first.strip()

    return ""


def build_member_map(group_detail):
    members = group_detail.get("members", [])
    result = {}

    for m in members:
        if not isinstance(m, dict):
            continue

        member_id = m.get("id")
        if member_id is None:
            continue

        name = member_name(m)
        if name:
            result[str(member_id)] = name

    return result


def extract_attendance(detail, member_map):
    responses = detail.get("responses", {}) or {}
    accepted = responses.get("acceptedIds", []) or []

    names = []
    missing_ids = []

    for mid in accepted:
        mid = str(mid)
        name = member_map.get(mid)

        if name:
            names.append(name)
        else:
            missing_ids.append(mid)

    if missing_ids:
        print("WARNING: acceptedIds without name match:", ", ".join(missing_ids))

    return sorted(set(names))


async def process_team(team):
    debug = team["slug"] == "vs"

    if debug:
        print("\n" + "=" * 70)
        print(f"TEAM: {team['name']}")
        print("=" * 70)
        print("slug:", team["slug"])
        print("group_id:", team["group_id"])
        print("output_file:", team["output_file"])
        print("home_address_keywords:", team["home_address_keywords"])

    email = team["email"]
    password = team["password"]
    group_id = team["group_id"]

    if not email or not password or not group_id:
        if debug:
            print("SKIP: incomplete config")
            print("email set:", bool(email))
            print("password set:", bool(password))
            print("group_id set:", bool(group_id))
        return

    client = spond.Spond(username=email, password=password)

    try:
        group = await client.get_group(group_id)
        member_map = build_member_map(group)

        if debug:
            print("DEBUG: group loaded successfully")
            print("DEBUG: total members:", len(group.get("members", [])))
            print("DEBUG: mapped member names:", len(member_map))

        events = await client.get_events(group_id)

        if debug:
            print("DEBUG: raw events returned from Spond:", len(events))
            print("DEBUG: event ids returned:", [str(e.get('id') or e.get('uid') or '') for e in events])

        output = []

        for index, evt in enumerate(events, start=1):
            norm = normalize_event(evt, team["home_address_keywords"])

            if debug:
                print("\n" + "-" * 70)
                print(f"EVENT #{index}")
                print("raw_event_keys:", sorted(list(evt.keys())))
                print("raw_event_json:", json.dumps(evt, ensure_ascii=False, default=str))
                print("id:", norm["id"])
                print("title:", norm["title"])
                print("raw_start:", norm["raw_start"])
                print("normalized_start:", norm["start"])
                print("raw_location_field:", json.dumps(evt.get("location"), ensure_ascii=False, default=str))
                print("location:", norm["location"] or "[empty]")
                print("matched_home_keywords:", norm["matched_home_keywords"])
                print("classified_as:", norm["type"])

            dt = parse_event_date(norm["start"])
            if not dt:
                if debug:
                    print("SKIP: no valid parsed date")
                continue

            if debug:
                print("event_date:", dt.date())
                print("today:", TODAY)

            if dt.date() < TODAY:
                if debug:
                    print("SKIP: event is in the past")
                continue

            if norm["type"] != "away":
                if debug:
                    print("SKIP: event classified as home")
                continue

            if debug:
                print("PASS: future away event -> fetching detail")

            detail = await client.get_event(norm["id"])
            attendees = extract_attendance(detail, member_map)
            norm["attending"] = attendees

            if debug:
                print("DEBUG: attending count:", len(attendees))
                if attendees:
                    print("DEBUG: attending names:", ", ".join(attendees))
                else:
                    print("DEBUG: no accepted attendees found")

            output.append(
                {
                    "id": norm["id"],
                    "title": norm["title"],
                    "start": norm["start"],
                    "location": norm["location"],
                    "type": norm["type"],
                    "attending": norm["attending"],
                }
            )

        output.sort(key=lambda x: x["start"])

        os.makedirs("feeds", exist_ok=True)

        payload = {
            "team": team["slug"],
            "generated": datetime.now(timezone.utc).isoformat(),
            "events": output,
        }

        with open(team["output_file"], "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        if debug:
            print("\n" + "=" * 70)
            print(f"RESULT TEAM {team['name']}")
            print("events_in_feed:", len(output))
            print(f"written_to: {team['output_file']}")
            print("=" * 70)

    finally:
        await client.clientsession.close()


async def main():
    for team in TEAMS:
        await process_team(team)


if __name__ == "__main__":
    asyncio.run(main())
