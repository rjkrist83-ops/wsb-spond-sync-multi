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
        "group_id": os.environ.get("SPOND_GROUP_ID_U12", "").strip(),
        "home_address_keywords": [
            "kaartenmakershoeve 108",
            "7326 xk",
        ],
        "only_away": True,
        "skip_without_date": True,
        "output_file": "feeds/u12.json",
    },
    {
        "slug": "u15",
        "name": "U15",
        "group_id": os.environ.get("SPOND_GROUP_ID_U15", "").strip(),
        "home_address_keywords": [
            "kaartenmakershoeve 108",
            "7326 xk",
        ],
        "only_away": True,
        "skip_without_date": True,
        "output_file": "feeds/u15.json",
    },
]


def fail(msg: str, code: int = 1):
    print(msg)
    sys.exit(code)


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
            locality_lower = locality.lower()
            if locality_lower not in address.lower():
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


def is_home_location(location_text, home_keywords):
    if not location_text:
        return False

    location_lower = location_text.lower()
    return any(keyword in location_lower for keyword in home_keywords)


def normalize_start(evt):
    candidates = [
        evt.get("startDate"),
        evt.get("start_date"),
        evt.get("startTime"),
        evt.get("start_time"),
        evt.get("start"),
        evt.get("date"),
        evt.get("from"),
        evt.get("startTimestamp"),
        evt.get("startsAt"),
        evt.get("startAt"),
    ]

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    return ""


def parse_event_date(value):
    if not value or not isinstance(value, str):
        return None

    value = value.strip()

    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    except ValueError:
        pass

    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt.endswith("Z"):
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue

    return None


def to_local_datetime(dt):
    if not dt:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(LOCAL_TZ)


def local_datetime_to_string(dt):
    if not dt:
        return ""

    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def normalize_event(evt, home_keywords):
    event_id = str(
        evt.get("id")
        or evt.get("eventId")
        or evt.get("uid")
        or ""
    )

    title = (
        evt.get("name")
        or evt.get("title")
        or evt.get("heading")
        or "Onbekend event"
    ).strip()

    start_source = normalize_start(evt)
    parsed_start = parse_event_date(start_source)
    local_start = to_local_datetime(parsed_start)
    start_local_string = local_datetime_to_string(local_start)

    location = normalize_location(evt.get("location") or evt.get("venue") or "")
    event_type = "home" if is_home_location(location, home_keywords) else "away"

    return {
        "id": event_id,
        "title": title,
        "start": start_local_string,
        "location": location,
        "type": event_type,
    }


def build_member_map(group_detail):
    member_map = {}

    if not isinstance(group_detail, dict):
        return member_map

    members = group_detail.get("members", [])
    if not isinstance(members, list):
        return member_map

    for member in members:
        if not isinstance(member, dict):
            continue

        member_id = str(
            member.get("id")
            or member.get("uid")
            or member.get("memberId")
            or ""
        ).strip()

        name = (
            member.get("name")
            or member.get("fullName")
            or member.get("firstName")
            or member.get("displayName")
            or ""
        ).strip()

        if member_id and name:
            member_map[member_id] = name

    return member_map


def extract_attendance_from_detail(detail, member_map):
    attendees = []

    if not isinstance(detail, dict):
        return attendees

    responses = detail.get("responses")
    if not isinstance(responses, dict):
        return attendees

    accepted_ids = responses.get("acceptedIds", [])
    if not isinstance(accepted_ids, list):
        return attendees

    for member_id in accepted_ids:
        member_id = str(member_id).strip()
        if not member_id:
            continue

        name = member_map.get(member_id)
        if name:
            attendees.append(name)
        else:
            attendees.append(f"UNKNOWN:{member_id}")

    return sorted(list(dict.fromkeys(attendees)))


async def get_event_detail_with_debug(client, event_id):
    try:
        detail = await client.get_event(event_id)
        if isinstance(detail, dict):
            print(f"DETAIL KEYS voor event {event_id}: {list(detail.keys())[:80]}")
        else:
            print(f"DETAIL TYPE voor event {event_id}: {type(detail)}")
        return detail
    except Exception as e:
        print(f"get_event mislukt voor {event_id}: {e}")
        return None


async def process_team(client, team):
    slug = team["slug"]
    name = team["name"]
    group_id = team["group_id"]
    home_keywords = team["home_address_keywords"]
    only_away = team["only_away"]
    skip_without_date = team["skip_without_date"]
    output_file = team["output_file"]

    print(f"\n=== TEAM START: {name} ({slug}) ===")
    print(f"GROUP_ID: {group_id or '(leeg)'}")
    print(f"ONLY_AWAY: {only_away}")
    print(f"SKIP_WITHOUT_DATE: {skip_without_date}")
    print(f"OUTPUT_FILE: {output_file}")
    print(f"HOME_ADDRESS_KEYWORDS: {home_keywords}")

    if not group_id:
        print(f"SKIP TEAM {slug}: geen group_id ingesteld")
        return

    groups = await client.get_groups()
    selected_group = None

    for g in groups:
        gid = str(g.get("id") or g.get("groupId") or g.get("uid") or "")
        if gid == group_id:
            selected_group = g
            break

    if not selected_group:
        print(f"SKIP TEAM {slug}: groep niet gevonden")
        return

    resolved_group_id = (
        selected_group.get("id")
        or selected_group.get("groupId")
        or selected_group.get("uid")
    )

    if not resolved_group_id:
        print(f"SKIP TEAM {slug}: kon group_id niet bepalen")
        return

    print(f"Geselecteerde groep: {resolved_group_id}")

    group_detail = await client.get_group(resolved_group_id)
    member_map = build_member_map(group_detail)
    print(f"Member map opgebouwd met {len(member_map)} leden")

    events = await client.get_events(resolved_group_id)
    print(f"get_events() gelukt, totaal ontvangen: {len(events) if events else 0}")

    payload_events = []

    for i, evt in enumerate(events or []):
        if not isinstance(evt, dict):
            continue

        norm = normalize_event(evt, home_keywords)
        event_dt = parse_event_date(norm["start"])

        if event_dt and event_dt.tzinfo is None:
            event_dt = event_dt.replace(tzinfo=LOCAL_TZ)

        local_event_date = event_dt.date() if event_dt else None

        print("----- EVENT DEBUG -----")
        print(f"TEAM: {slug}")
        print(f"TITEL: {norm['title']}")
        print(f"EVENT ID: {norm['id']}")
        print(f"START RAW (local): {norm['start']}")
        print(f"PARSED DATE: {event_dt}")
        print(f"LOCAL EVENT DATE: {local_event_date}")
        print(f"LOCATIE: {norm['location']}")
        print(f"TYPE: {norm['type']}")

        if skip_without_date and not event_dt:
            print("RESULT: OVERGESLAGEN - GEEN DATUM")
            continue

        if local_event_date and local_event_date < TODAY:
            print(f"RESULT: OVERGESLAGEN - IN HET VERLEDEN ({local_event_date})")
            continue

        if only_away and norm["type"] != "away":
            print("RESULT: OVERGESLAGEN - HOME EVENT")
            continue

        detail = await get_event_detail_with_debug(client, norm["id"])
        attendees = extract_attendance_from_detail(detail, member_map)

        if isinstance(detail, dict):
            responses = detail.get("responses")
            if isinstance(responses, dict):
                accepted_ids = responses.get("acceptedIds", [])
                print(f"ACCEPTED IDS COUNT: {len(accepted_ids) if isinstance(accepted_ids, list) else 0}")

        print(f"ATTENDING COUNT: {len(attendees)}")
        if attendees:
            print(f"ATTENDING PREVIEW: {attendees[:15]}")

        norm["attending"] = attendees
        print("RESULT: OPNEMEN")
        payload_events.append(norm)

    payload_events.sort(key=lambda x: x.get("start", ""))

    print(f"Events in payload na filter ({slug}): {len(payload_events)}")
    if payload_events:
        print("Eerste event preview:")
        print(json.dumps(payload_events[0], ensure_ascii=False)[:1200])

    payload = {
        "source": "github-actions-spond-sync-multi",
        "team": slug,
        "team_name": name,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "generated_for_date": TODAY.isoformat(),
        "generated_timezone": "Europe/Amsterdam",
        "group_id": str(resolved_group_id),
        "home_address_keywords": home_keywords,
        "events": payload_events,
    }

    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"JSON geschreven naar: {output_file}")
    print(f"=== TEAM KLAAR: {name} ({slug}) ===")


async def async_main():
    spond_email = os.environ.get("SPOND_EMAIL", "").strip()
    spond_password = os.environ.get("SPOND_PASSWORD", "").strip()

    print("=== DEBUG START ===")
    print(f"SPOND_EMAIL aanwezig: {'ja' if bool(spond_email) else 'nee'}")
    print(f"SPOND_PASSWORD aanwezig: {'ja' if bool(spond_password) else 'nee'}")
    print(f"LOCAL_TZ: {LOCAL_TZ}")
    print(f"TODAY: {TODAY.isoformat()}")
    print(f"Aantal teams in config: {len(TEAMS)}")
    print("=== DEBUG EINDE BASIS ===")

    if not spond_email:
        fail("SPOND_EMAIL ontbreekt")
    if not spond_password:
        fail("SPOND_PASSWORD ontbreekt")

    client = None

    try:
        client = spond.Spond(username=spond_email, password=spond_password)
        print("Spond client initialisatie gelukt")

        for team in TEAMS:
            await process_team(client, team)

    finally:
        if client and hasattr(client, "clientsession") and client.clientsession:
            await client.clientsession.close()


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
