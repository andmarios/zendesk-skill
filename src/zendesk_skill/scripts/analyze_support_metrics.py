#!/usr/bin/env python3
"""Support metrics analysis script.

Analyzes tickets from search results to generate comprehensive metrics including:
- Tickets per customer (by email domain)
- Messages per customer and per ticket
- Call detection
- FRT and resolution time statistics (with proper time basis for on-call vs business hours)
- Status and priority breakdown
- FRT breakdown by priority

Usage:
    python analyze_support_metrics.py [search_results_file] [--start DATE] [--end DATE] [--output DIR]

Options:
    --include-untouched  Include tickets where support didn't reply (default: filter to tickets with replies)
    --fetch-metrics      Fetch ticket metrics from API (required for accurate reply counts and FRT)

If no arguments provided, uses most recent search file in temp directory.
Default period is 2 weeks ending today.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

# Add parent to path to import client
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from zendesk_skill.client import get_business_hours_config


def get_domain(email: str) -> str:
    """Extract domain from email address."""
    if not email or "@" not in email:
        return "unknown"
    return email.split("@")[1].lower()


def parse_timestamp(ts: str, tz: ZoneInfo) -> datetime | None:
    """Parse ISO timestamp and convert to specified timezone."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(tz)
    except (ValueError, TypeError):
        return None


def is_business_hours(dt: datetime, config: dict, tz: ZoneInfo) -> bool:
    """Check if datetime is within business hours.

    Args:
        dt: Datetime to check
        config: Business hours config with start_hour, end_hour, workdays
        tz: Timezone for conversion
    """
    if dt is None:
        return True  # Assume business hours if unknown
    local_dt = dt.astimezone(tz) if dt.tzinfo else dt

    start_hour = config.get("start_hour", 9)
    end_hour = config.get("end_hour", 18)
    workdays = config.get("workdays", [0, 1, 2, 3, 4])  # Mon-Fri default

    # Check if workday
    if local_dt.weekday() not in workdays:
        return False
    # Check if within hours
    if local_dt.hour < start_hour or local_dt.hour >= end_hour:
        return False
    return True


def is_oncall_hours(dt: datetime, config: dict, tz: ZoneInfo, workdays: list[int]) -> bool:
    """Check if datetime is during on-call hours.

    Args:
        dt: Datetime to check
        config: On-call config with start_hour, end_hour
        tz: Timezone for conversion
        workdays: List of workday integers (0=Monday)
    """
    if dt is None:
        return False
    local_dt = dt.astimezone(tz) if dt.tzinfo else dt

    start_hour = config.get("start_hour", 19)
    end_hour = config.get("end_hour", 9)

    # Non-workday = always on-call
    if local_dt.weekday() not in workdays:
        return True
    # Workday: check on-call hours (e.g., 7 PM - 9 AM)
    if local_dt.hour >= start_hour or local_dt.hour < end_hour:
        return True
    return False


def _calculate_business_minutes(start: datetime, end: datetime, config: dict, tz: ZoneInfo) -> float:
    """Calculate minutes between two datetimes counting only business hours.

    Walks through each minute from start to end and counts only those
    that fall within business hours. For accuracy without excessive iteration,
    we walk day-by-day and calculate overlap with business hours per day.
    """
    start_hour = config.get("start_hour", 9)
    end_hour = config.get("end_hour", 18)
    workdays = config.get("workdays", [0, 1, 2, 3, 4])

    start_local = start.astimezone(tz)
    end_local = end.astimezone(tz)

    if start_local >= end_local:
        return 0.0

    total_minutes = 0.0
    current = start_local

    while current < end_local:
        # If not a workday, skip to next day
        if current.weekday() not in workdays:
            next_day = current.replace(hour=0, minute=0, second=0) + timedelta(days=1)
            current = next_day
            continue

        # Business window for this day
        biz_start = current.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        biz_end = current.replace(hour=end_hour, minute=0, second=0, microsecond=0)

        # Overlap between [current, end_local] and [biz_start, biz_end]
        overlap_start = max(current, biz_start)
        overlap_end = min(end_local, biz_end)

        if overlap_start < overlap_end:
            total_minutes += (overlap_end - overlap_start).total_seconds() / 60

        # Move to next day
        next_day = current.replace(hour=0, minute=0, second=0) + timedelta(days=1)
        current = next_day

    return total_minutes


def detect_calls(comments: list) -> dict:
    """Detect and count calls/meetings from comments.

    Returns dict with:
        - detected: bool - whether any call indicators found
        - confirmed: int - number of confirmed calls (evidence call happened)
        - likely: int - number of likely calls (link + setup but no confirmation)
        - requests: int - number of call requests (no evidence call happened)
        - total_estimated: int - confirmed + likely
        - links: list - unique meeting links found
        - evidence: list - evidence phrases found for confirmed calls
        - link_details: list - dict with platform and link for likely calls
    """
    # Patterns indicating a call was set up or requested
    setup_patterns = re.compile(
        r'quick call|setup a call|set up a call|schedule[d]? (?:a )?call|'
        r'let.*s call|call to discuss|join.*(?:the |this )?(?:call|zoom|meeting)|'
        r'meeting invitation|video call|phone call|can.*call|available for.*call|'
        r'connect (?:in|on)',
        re.IGNORECASE
    )

    # Patterns indicating a call actually happened (with capture for evidence)
    happened_patterns = re.compile(
        r'((?:we |our )?(?:call|meeting) (?:closed|ended|finished)|'
        r'we closed our call|after (?:the|our) (?:call|meeting)|'
        r'meeting notes|following (?:the|our) (?:call|meeting)|'
        r'spoke (?:on|with|to|about)|talked (?:on|with|to)|'
        r'on (?:the|our) call|during (?:the|our) call|state after.*call)',
        re.IGNORECASE
    )

    # Meeting link patterns with platform detection
    zoom_pattern = re.compile(r'(zoom\.us/[jm]/\d+)', re.IGNORECASE)
    teams_pattern = re.compile(r'(teams\.microsoft\.com[^\s]*)', re.IGNORECASE)
    meet_pattern = re.compile(r'(meet\.google\.com[^\s]*)', re.IGNORECASE)

    # Duration patterns (e.g., "4.5-hour call", "2.5 hours", "45 minute call")
    duration_pattern = re.compile(
        r'(\d+(?:\.\d+)?)\s*[-.]?\s*hours?\s*(?:call|meeting)|'
        r'(\d+(?:\.\d+)?)\s*[-.]?\s*minutes?\s*(?:call|meeting)|'
        r'(?:call|meeting)\s*(?:lasted|took|was)\s*(?:about\s*)?(\d+(?:\.\d+)?)\s*hours?|'
        r'(?:call|meeting)\s*(?:lasted|took|was)\s*(?:about\s*)?(\d+)\s*minutes?',
        re.IGNORECASE
    )

    # False positive patterns to exclude
    exclude_patterns = re.compile(
        r'callback|recall|localhost|api\.call|function.?call|method.?call|system.?call',
        re.IGNORECASE
    )

    setup_count = 0
    happened_count = 0
    links = set()
    evidence_phrases = []
    link_details = []
    call_dates = []  # Dates when call evidence was found
    call_durations = []  # Durations mentioned (in minutes)

    for comment in comments:
        body = comment.get("plain_body") or comment.get("body") or ""
        comment_date = (comment.get("created_at") or "")[:10]  # YYYY-MM-DD

        if exclude_patterns.search(body):
            continue

        if setup_patterns.search(body):
            setup_count += 1

        # Capture call durations
        dur_matches = duration_pattern.findall(body)
        for dur_match in dur_matches:
            # Groups: (hours, minutes, hours_alt, minutes_alt)
            hours = dur_match[0] or dur_match[2]
            minutes = dur_match[1] or dur_match[3]
            if hours:
                call_durations.append(float(hours) * 60)
            elif minutes:
                call_durations.append(float(minutes))

        # Capture evidence phrases
        happened_matches = happened_patterns.findall(body)
        if happened_matches:
            happened_count += len(happened_matches)
            evidence_phrases.extend(happened_matches)
            if comment_date:
                call_dates.append(comment_date)

        # Capture links with platform info
        for match in zoom_pattern.findall(body):
            links.add(match)
            link_details.append({"platform": "Zoom", "link": match, "date": comment_date})
        for match in teams_pattern.findall(body):
            link = match.split()[0]  # Take just the URL part
            links.add(link)
            link_details.append({"platform": "Teams", "link": link, "date": comment_date})
        for match in meet_pattern.findall(body):
            link = match.split()[0]
            links.add(link)
            link_details.append({"platform": "Meet", "link": link, "date": comment_date})

    # Estimate actual calls
    if happened_count > 0:
        # Confirmed - we have evidence call happened
        confirmed = max(happened_count, 1)
        likely = 0
    elif links and setup_count > 0:
        # Likely - zoom link shared with setup discussion
        confirmed = 0
        likely = 1
    else:
        confirmed = 0
        likely = 0

    requests = setup_count if confirmed == 0 and likely == 0 else 0
    total_estimated = confirmed + likely
    detected = total_estimated > 0 or requests > 0

    # Deduplicate link_details
    seen_links = set()
    unique_link_details = []
    for ld in link_details:
        if ld["link"] not in seen_links:
            seen_links.add(ld["link"])
            unique_link_details.append(ld)

    return {
        "detected": detected,
        "confirmed": confirmed,
        "likely": likely,
        "requests": requests,
        "total_estimated": total_estimated,
        "links": list(links),
        "evidence": list(set(evidence_phrases))[:3],  # Top 3 unique evidence phrases
        "link_details": unique_link_details[:3],  # Top 3 unique links
        "call_dates": sorted(set(call_dates)),  # Unique dates when calls happened
        "call_durations": call_durations,  # Durations in minutes
        "total_call_duration_mins": sum(call_durations) if call_durations else None,
    }


def mins_to_human(mins: float | None) -> str:
    """Convert minutes to human-readable format."""
    if mins is None:
        return "N/A"
    if mins < 60:
        return f"{int(mins)}m"
    elif mins < 1440:
        return f"{mins/60:.1f}h"
    else:
        return f"{mins/1440:.1f}d"


def fetch_ticket_metrics(ticket_ids: list[int], base_dir: Path, skill_dir: Path) -> dict[int, dict]:
    """Fetch ticket metrics for all tickets to get accurate reply counts and FRT.

    Args:
        ticket_ids: List of ticket IDs to fetch metrics for
        base_dir: Directory where metrics will be saved
        skill_dir: Path to zendesk-skill directory (for running uv commands)

    Returns:
        Dict mapping ticket_id -> metrics dict
    """
    all_metrics = {}
    total = len(ticket_ids)

    for i, tid in enumerate(ticket_ids):
        if i % 20 == 0:
            print(f"Fetching metrics: {i}/{total}...")

        # Check if already fetched
        ticket_dir = base_dir / str(tid)
        existing_metrics = list(ticket_dir.glob("ticket_metrics_*.json")) if ticket_dir.exists() else []
        if existing_metrics:
            with open(existing_metrics[0]) as f:
                data = json.load(f)
                metric = data.get("data", {}).get("ticket_metric", {})
                all_metrics[tid] = {
                    "ticket_id": tid,
                    "replies": metric.get("replies", 0),
                    "frt_calendar": metric.get("reply_time_in_minutes", {}).get("calendar"),
                    "frt_business": metric.get("reply_time_in_minutes", {}).get("business"),
                    "resolution_calendar": metric.get("full_resolution_time_in_minutes", {}).get("calendar"),
                    "resolution_business": metric.get("full_resolution_time_in_minutes", {}).get("business"),
                    "reopens": metric.get("reopens", 0),
                }
            continue

        # Fetch from API
        result = subprocess.run(
            ["uv", "run", "zendesk", "ticket-metrics", str(tid)],
            capture_output=True, text=True, cwd=str(skill_dir)
        )
        if result.returncode == 0:
            try:
                # The CLI saves to file, but we parse stdout for consistency
                output = json.loads(result.stdout)
                metric = output.get("data", {}).get("ticket_metric", {})
                all_metrics[tid] = {
                    "ticket_id": tid,
                    "replies": metric.get("replies", 0),
                    "frt_calendar": metric.get("reply_time_in_minutes", {}).get("calendar"),
                    "frt_business": metric.get("reply_time_in_minutes", {}).get("business"),
                    "resolution_calendar": metric.get("full_resolution_time_in_minutes", {}).get("calendar"),
                    "resolution_business": metric.get("full_resolution_time_in_minutes", {}).get("business"),
                    "reopens": metric.get("reopens", 0),
                }
            except json.JSONDecodeError:
                print(f"Warning: Failed to parse metrics for ticket {tid}", file=sys.stderr)

    print(f"Fetched metrics for {len(all_metrics)} tickets")
    return all_metrics


def calculate_frt_by_priority(
    ticket_data: list[dict],
    metrics: dict[int, dict],
    oncall_customers: list[str],
    oncall_priorities: list[str],
) -> dict:
    """Calculate FRT breakdown by priority using proper time basis.

    For tickets matching on-call criteria (priority + customer), uses calendar time (24/7).
    For all other tickets, uses business hours time.

    Args:
        ticket_data: List of ticket analysis dicts (with customer, priority)
        metrics: Dict of ticket_id -> metrics
        oncall_customers: List of customer domains for on-call (empty = all)
        oncall_priorities: List of priority values for on-call (e.g., ["urgent"])

    Returns:
        Dict with FRT stats per category
    """
    # Group tickets by FRT category
    categories = {
        "oncall": [],      # On-call priority + customer (uses calendar time)
        "urgent": [],      # Urgent not matching on-call (uses business time)
        "high": [],        # High priority (uses business time)
        "normal": [],      # Normal priority (uses business time)
        "low": [],         # Low priority (uses business time)
    }

    for ticket in ticket_data:
        tid = ticket["ticket_id"]
        priority = ticket.get("priority", "normal")
        customer = ticket.get("customer", "unknown")

        # Use comment-based FRT from ticket_data first, fallback to metrics API
        frt_calendar = ticket.get("frt_calendar")
        frt_business = ticket.get("frt_business")

        if frt_calendar is None:
            metric = metrics.get(tid, {})
            if metric.get("replies", 0) == 0:
                continue
            frt_calendar = metric.get("frt_calendar")
            frt_business = metric.get("frt_business")

        if frt_calendar is None:
            continue

        # Determine if this is an on-call ticket
        priority_match = priority in oncall_priorities
        customer_match = len(oncall_customers) == 0 or customer in oncall_customers
        is_oncall = priority_match and customer_match

        if is_oncall:
            # On-call tickets use calendar time (24/7 coverage)
            categories["oncall"].append({
                "ticket_id": tid,
                "priority": priority,
                "customer": customer,
                "frt": frt_calendar,
            })
        elif priority == "urgent":
            # Non-on-call urgent uses business hours
            categories["urgent"].append({
                "ticket_id": tid,
                "customer": customer,
                "frt": frt_business if frt_business else frt_calendar,
            })
        elif priority == "high":
            categories["high"].append({
                "ticket_id": tid,
                "frt": frt_business if frt_business else frt_calendar,
            })
        elif priority == "low":
            categories["low"].append({
                "ticket_id": tid,
                "frt": frt_business if frt_business else frt_calendar,
            })
        else:  # normal
            categories["normal"].append({
                "ticket_id": tid,
                "frt": frt_business if frt_business else frt_calendar,
            })

    # Calculate stats per category
    results = {}
    for cat_name, cat_data in categories.items():
        if not cat_data:
            results[cat_name] = {"count": 0}
            continue

        frt_list = [d["frt"] for d in cat_data if d["frt"] is not None]
        if not frt_list:
            results[cat_name] = {"count": len(cat_data)}
            continue

        results[cat_name] = {
            "count": len(cat_data),
            "avg_mins": mean(frt_list),
            "median_mins": median(frt_list),
            "min_mins": min(frt_list),
            "max_mins": max(frt_list),
            "under_30m": sum(1 for f in frt_list if f <= 30),
            "under_1h": sum(1 for f in frt_list if f <= 60),
            "under_4h": sum(1 for f in frt_list if f <= 240),
            "under_8h": sum(1 for f in frt_list if f <= 480),
        }

    return results


def main():
    base_dir = Path(tempfile.gettempdir()) / "zendesk-skill"

    # Parse arguments
    parser = argparse.ArgumentParser(description="Analyze support metrics from Zendesk search results")
    parser.add_argument("search_file", nargs="?", help="Search results JSON file")
    parser.add_argument("--start", help="Period start date (YYYY-MM-DD). Default: 14 days ago")
    parser.add_argument("--end", help="Period end date (YYYY-MM-DD). Default: today")
    parser.add_argument("--output", "-o", help="Output directory")
    parser.add_argument(
        "--include-untouched",
        action="store_true",
        help="Include tickets without agent replies (default: only tickets we replied to)"
    )
    parser.add_argument(
        "--fetch-metrics",
        action="store_true",
        help="Fetch ticket metrics from API (required for accurate reply counts and FRT)"
    )
    args = parser.parse_args()

    # Find search file
    if args.search_file:
        search_file = Path(args.search_file)
    else:
        search_files = list(base_dir.glob("search_*.json"))
        if not search_files:
            print("No search results found")
            raise SystemExit(1)
        search_file = max(search_files, key=lambda f: f.stat().st_mtime)

    output_dir = Path(args.output) if args.output else base_dir

    # Calculate period (default: 2 weeks)
    today = datetime.now()
    if args.end:
        end_date = datetime.strptime(args.end, "%Y-%m-%d")
    else:
        end_date = today
    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
    else:
        start_date = end_date - timedelta(days=14)

    # Load business hours config (optional)
    bh_config = get_business_hours_config()
    track_business_hours = bh_config is not None
    tz = None
    bh_settings = None
    oncall_settings = None
    workdays = [0, 1, 2, 3, 4]  # Default Mon-Fri

    if track_business_hours:
        bh_settings = bh_config.get("business_hours", {})
        oncall_settings = bh_config.get("oncall", {})
        tz = ZoneInfo(bh_settings.get("timezone", "Europe/Berlin"))
        workdays = bh_settings.get("workdays", [0, 1, 2, 3, 4])
    else:
        tz = ZoneInfo("UTC")

    # Make period dates timezone-aware for comment filtering
    period_start = start_date.replace(tzinfo=tz)
    period_end = (end_date + timedelta(days=1)).replace(tzinfo=tz)  # End of end_date (inclusive)

    period_info = {
        "start_date": start_date.strftime("%b %d, %Y"),
        "end_date": end_date.strftime("%b %d, %Y"),
        "days": (end_date - start_date).days,
    }

    print(f"Using search file: {search_file}")
    print(f"Period: {period_info['start_date']} – {period_info['end_date']} ({period_info['days']} days)")

    with open(search_file) as f:
        search_data = json.load(f)

    tickets = search_data.get("data", {}).get("results", [])
    print(f"Found {len(tickets)} tickets in search results")

    # Skill directory for running uv commands
    skill_dir = Path(__file__).parent.parent.parent.parent

    # Fetch metrics for all tickets if requested (enables accurate reply counts)
    all_metrics = {}
    if args.fetch_metrics:
        print("\nFetching ticket metrics for accurate reply counts...")
        ticket_ids = [t.get("id") for t in tickets if t.get("id")]
        all_metrics = fetch_ticket_metrics(ticket_ids, base_dir, skill_dir)

    # Filter to tickets with replies (unless --include-untouched)
    if not args.include_untouched:
        if all_metrics:
            # Use fetched metrics for accurate filtering
            tickets_with_replies = {tid for tid, m in all_metrics.items() if m.get("replies", 0) > 0}
            original_count = len(tickets)
            tickets = [t for t in tickets if t.get("id") in tickets_with_replies]
            print(f"Filtered to {len(tickets)} tickets with agent replies (excluded {original_count - len(tickets)} untouched)")
        else:
            # Without metrics, check if metrics files exist locally
            filtered = []
            for t in tickets:
                tid = t.get("id")
                ticket_dir = base_dir / str(tid)
                metrics_files = list(ticket_dir.glob("ticket_metrics_*.json")) if ticket_dir.exists() else []
                if metrics_files:
                    with open(metrics_files[0]) as f:
                        data = json.load(f)
                        replies = data.get("data", {}).get("ticket_metric", {}).get("replies", 0)
                        if replies > 0:
                            filtered.append(t)
                            all_metrics[tid] = {
                                "ticket_id": tid,
                                "replies": replies,
                                "frt_calendar": data.get("data", {}).get("ticket_metric", {}).get("reply_time_in_minutes", {}).get("calendar"),
                                "frt_business": data.get("data", {}).get("ticket_metric", {}).get("reply_time_in_minutes", {}).get("business"),
                                "resolution_calendar": data.get("data", {}).get("ticket_metric", {}).get("full_resolution_time_in_minutes", {}).get("calendar"),
                                "resolution_business": data.get("data", {}).get("ticket_metric", {}).get("full_resolution_time_in_minutes", {}).get("business"),
                                "reopens": data.get("data", {}).get("ticket_metric", {}).get("reopens", 0),
                            }
                else:
                    # If no metrics, include ticket (can't verify reply count)
                    filtered.append(t)
            tickets = filtered
            print(f"Filtered to {len(tickets)} tickets (based on local metrics files)")

    print(f"Analyzing {len(tickets)} tickets")

    # Get unique requester IDs and fetch emails
    requester_ids = set(t.get("requester_id") for t in tickets if t.get("requester_id"))
    print(f"Unique requesters: {len(requester_ids)}")

    user_emails = {}
    for rid in requester_ids:
        user_files = list(base_dir.glob(f"**/user_*{rid}*.json"))
        if user_files:
            with open(user_files[0]) as f:
                user_data = json.load(f)
                user_emails[rid] = user_data.get("data", {}).get("user", {}).get("email", "")
        else:
            result = subprocess.run(
                ["uv", "run", "zendesk", "user", str(rid)],
                capture_output=True, text=True,
                cwd=str(Path(__file__).parent.parent.parent.parent)
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                user_emails[rid] = data.get("email", "")

    print(f"Resolved {len(user_emails)} user emails")

    # Analyze tickets
    ticket_analysis = []
    customer_stats = defaultdict(lambda: {"tickets": 0, "messages": 0, "replies": 0, "calls": 0, "ticket_ids": []})
    status_counts = defaultdict(int)
    priority_counts = defaultdict(int)
    frt_values = []
    resolution_values = []
    reopen_count = 0

    # New vs existing ticket tracking
    new_ticket_count = 0
    existing_ticket_count = 0

    # Business hours tracking
    tickets_outside_hours = []
    customer_msgs_outside_hours = 0
    support_replies_outside_hours = 0
    oncall_engagements = []

    for ticket in tickets:
        tid = ticket.get("id")
        rid = ticket.get("requester_id")
        status = ticket.get("status", "unknown")
        priority = ticket.get("priority", "normal")

        status_counts[status] += 1
        priority_counts[priority] += 1

        # Classify as new (created in period) or existing (created before period)
        created_at = parse_timestamp(ticket.get("created_at"), tz)
        is_new_ticket = created_at and created_at >= period_start
        if is_new_ticket:
            new_ticket_count += 1
        else:
            existing_ticket_count += 1

        # Check if ticket created outside business hours (only for new tickets)
        ticket_outside_hours = False
        if track_business_hours and created_at and is_new_ticket:
            ticket_outside_hours = not is_business_hours(created_at, bh_settings, tz)

        # Find ticket details file
        ticket_dir = base_dir / str(tid)
        details_file = None
        metrics_file = None

        if ticket_dir.exists():
            for f in ticket_dir.glob("ticket_details_*.json"):
                details_file = f
                break
            for f in ticket_dir.glob("ticket_metrics_*.json"):
                metrics_file = f
                break

        # Analyze comments
        msg_count = 0
        public_count = 0
        private_count = 0
        agent_reply_count = 0
        call_info = {"detected": False, "confirmed": 0, "likely": 0, "requests": 0, "total_estimated": 0, "links": []}
        ticket_customer_msgs_ooh = 0
        ticket_support_replies_ooh = 0

        if details_file:
            with open(details_file) as f:
                details = json.load(f)
            all_comments = details.get("data", {}).get("comments", [])

            # Filter comments to only those within the reporting period
            comments = []
            for c in all_comments:
                c_time = parse_timestamp(c.get("created_at"), tz)
                if c_time and period_start <= c_time < period_end:
                    comments.append(c)

            msg_count = len(comments)
            public_count = sum(1 for c in comments if c.get("public", True))
            private_count = msg_count - public_count
            # Count agent replies: public comments not from the requester
            agent_reply_count = sum(
                1 for c in comments
                if c.get("public", True) and c.get("author_id") != rid
            )
            call_info = detect_calls(comments)

            # Analyze each comment for business hours (only if configured)
            if track_business_hours:
                for comment in comments:
                    comment_time = parse_timestamp(comment.get("created_at"), tz)
                    is_public = comment.get("public", True)
                    author_id = comment.get("author_id")

                    if comment_time and not is_business_hours(comment_time, bh_settings, tz):
                        # Check if customer (requester) or support
                        if author_id == rid:
                            ticket_customer_msgs_ooh += 1
                            customer_msgs_outside_hours += 1
                        elif is_public:  # Support reply (public, not from requester)
                            ticket_support_replies_ooh += 1
                            support_replies_outside_hours += 1

        # Calculate FRT from comments for new tickets
        frt_calendar = None
        frt_business = None
        frt = None
        resolution = None
        reopens = 0
        replies = 0

        if is_new_ticket and details_file and created_at:
            # Find first public agent reply from ALL comments (not filtered by period)
            first_agent_reply_time = None
            for c in sorted(all_comments, key=lambda x: x.get("created_at", "")):
                if c.get("public", True) and c.get("author_id") != rid:
                    reply_time = parse_timestamp(c.get("created_at"), tz)
                    if reply_time and reply_time > created_at:
                        first_agent_reply_time = reply_time
                        break

            if first_agent_reply_time:
                # Calendar FRT = wall clock difference in minutes
                frt_calendar = (first_agent_reply_time - created_at).total_seconds() / 60

                # Business hours FRT = only count minutes during business hours
                if track_business_hours:
                    frt_business = _calculate_business_minutes(
                        created_at, first_agent_reply_time, bh_settings, tz
                    )

        # Fallback to metrics API if available and no comment-based FRT
        if frt_calendar is None:
            if tid in all_metrics:
                metric = all_metrics[tid]
                frt_calendar = metric.get("frt_calendar")
                frt_business = metric.get("frt_business")
                resolution = metric.get("resolution_calendar")
                reopens = metric.get("reopens", 0)
                replies = metric.get("replies", 0)
            elif metrics_file:
                with open(metrics_file) as f:
                    metrics_data = json.load(f)
                metric = metrics_data.get("data", {}).get("ticket_metric", {})
                frt_calendar = metric.get("reply_time_in_minutes", {}).get("calendar")
                frt_business = metric.get("reply_time_in_minutes", {}).get("business")
                resolution = metric.get("full_resolution_time_in_minutes", {}).get("calendar")
                reopens = metric.get("reopens", 0)
                replies = metric.get("replies", 0)

        # Determine FRT based on on-call status
        oncall_priorities_list = oncall_settings.get("priorities", ["urgent"]) if oncall_settings else ["urgent"]
        oncall_customers_list = oncall_settings.get("customers", []) if oncall_settings else []

        priority_match = priority in oncall_priorities_list
        # Customer is set below, so we'll calculate FRT after customer is resolved
        # For now, store both values
        frt = frt_calendar  # Will be recalculated later with proper time basis

        if frt_calendar is not None:
            frt_values.append(frt_calendar)  # For backward compatibility
        if resolution is not None:
            resolution_values.append(resolution)
        if reopens > 0:
            reopen_count += 1

        # Customer stats (need email/customer before ticket_analysis)
        email = user_emails.get(rid, "")
        customer = get_domain(email)

        ticket_analysis.append({
            "ticket_id": tid,
            "requester_id": rid,
            "subject": ticket.get("subject", "")[:40],
            "status": status,
            "priority": priority,
            "messages": msg_count,
            "public": public_count,
            "private": private_count,
            "call_info": call_info,
            "frt_mins": frt,
            "frt_calendar": frt_calendar,
            "frt_business": frt_business,
            "resolution_mins": resolution,
            "reopens": reopens,
            "replies": replies,
            "agent_replies": agent_reply_count,
            "outside_hours": ticket_outside_hours,
            "customer_msgs_ooh": ticket_customer_msgs_ooh,
            "support_replies_ooh": ticket_support_replies_ooh,
            "customer": customer,
            "is_new": is_new_ticket,
        })

        # Track tickets outside business hours (only if configured)
        if track_business_hours and ticket_outside_hours:
            tickets_outside_hours.append({
                "ticket_id": tid,
                "subject": ticket.get("subject", "")[:40],
                "created_at": ticket.get("created_at"),
                "priority": priority,
                "customer": customer,
            })

        # Check for on-call engagement (only if configured and enabled)
        if track_business_hours and oncall_settings and oncall_settings.get("enabled"):
            oncall_priorities = oncall_settings.get("priorities", ["urgent"])
            oncall_customers = oncall_settings.get("customers", [])

            # Check if priority matches
            priority_match = priority in oncall_priorities
            # Empty customers = all customers; otherwise check if in list
            customer_match = len(oncall_customers) == 0 or customer in oncall_customers

            if priority_match and customer_match and is_oncall_hours(created_at, oncall_settings, tz, workdays) and is_new_ticket:
                oncall_engagements.append({
                    "ticket_id": tid,
                    "subject": ticket.get("subject", "")[:40],
                    "created_at": ticket.get("created_at"),
                    "created_at_local": created_at.strftime("%Y-%m-%d %H:%M %Z") if created_at else None,
                    "customer": customer,
                    "priority": priority,
                })

        # Customer stats
        customer_stats[customer]["tickets"] += 1
        customer_stats[customer]["messages"] += msg_count
        customer_stats[customer]["replies"] += agent_reply_count
        customer_stats[customer]["ticket_ids"].append(tid)
        customer_stats[customer]["calls"] += call_info["total_estimated"]

    # Calculate FRT stats (overall - using calendar time for backward compatibility)
    frt_stats = {}
    if frt_values:
        frt_stats = {
            "avg_mins": sum(frt_values) / len(frt_values),
            "min_mins": min(frt_values),
            "max_mins": max(frt_values),
            "median_mins": sorted(frt_values)[len(frt_values) // 2],
            "count": len(frt_values),
        }

    # Calculate FRT by priority using proper time basis
    oncall_customers_list = oncall_settings.get("customers", []) if oncall_settings else []
    oncall_priorities_list = oncall_settings.get("priorities", ["urgent"]) if oncall_settings else ["urgent"]
    frt_by_priority = calculate_frt_by_priority(
        ticket_analysis, all_metrics, oncall_customers_list, oncall_priorities_list
    )

    resolution_stats = {}
    if resolution_values:
        resolution_stats = {
            "avg_mins": sum(resolution_values) / len(resolution_values),
            "min_mins": min(resolution_values),
            "max_mins": max(resolution_values),
            "count": len(resolution_values),
        }

    # Calculate total replies from comments (agent replies in period)
    total_replies = sum(t.get("agent_replies", 0) for t in ticket_analysis)

    # Build output (period_info comes from command line args)
    output = {
        "ticket_analysis": ticket_analysis,
        "customer_stats": dict(customer_stats),
        "summary": {
            "total_tickets": len(tickets),
            "new_tickets": new_ticket_count,
            "existing_tickets": existing_ticket_count,
            "total_messages": sum(t["messages"] for t in ticket_analysis),
            "total_replies": total_replies,
            "tickets_with_calls": sum(1 for t in ticket_analysis if t["call_info"]["total_estimated"] > 0),
            "total_calls_confirmed": sum(t["call_info"]["confirmed"] for t in ticket_analysis),
            "total_calls_likely": sum(t["call_info"]["likely"] for t in ticket_analysis),
            "total_calls_estimated": sum(t["call_info"]["total_estimated"] for t in ticket_analysis),
            "unique_customers": len(customer_stats),
        },
        "period": period_info,
        "status_breakdown": dict(status_counts),
        "priority_breakdown": dict(priority_counts),
        "frt_stats": frt_stats,
        "frt_by_priority": frt_by_priority,
        "resolution_stats": resolution_stats,
        "reopen_count": reopen_count,
    }

    # Add business hours data only if configured
    if track_business_hours:
        output["business_hours"] = {
            "config": bh_settings,
            "tickets_outside_hours": len(tickets_outside_hours),
            "tickets_outside_hours_list": tickets_outside_hours,
            "customer_msgs_outside_hours": customer_msgs_outside_hours,
            "support_replies_outside_hours": support_replies_outside_hours,
        }
        if oncall_settings and oncall_settings.get("enabled"):
            output["oncall"] = {
                "config": oncall_settings,
                "engagements": oncall_engagements,
            }

    # Build detailed call analysis
    confirmed_detail = []
    likely_detail = []
    call_by_customer: dict[str, dict] = {}

    for t in ticket_analysis:
        call_info = t.get("call_info", {})
        customer = t.get("customer", "unknown")

        # Track calls by customer
        if customer not in call_by_customer:
            call_by_customer[customer] = {"tickets": 0, "calls": 0}
        call_by_customer[customer]["tickets"] += 1
        if call_info.get("total_estimated", 0) > 0:
            call_by_customer[customer]["calls"] += call_info["total_estimated"]

        # Collect confirmed call details
        if call_info.get("confirmed", 0) > 0:
            evidence = call_info.get("evidence", [])
            evidence_str = " + ".join(evidence[:2]) if evidence else "Evidence in comments"
            # Check for links to add to evidence
            if call_info.get("links"):
                link_type = "zoom link" if any("zoom" in l.lower() for l in call_info["links"]) else "meeting link"
                evidence_str = f'"{evidence[0]}" + {link_type}' if evidence else link_type
            # Build duration string if available
            duration_str = None
            if call_info.get("total_call_duration_mins"):
                total_mins = call_info["total_call_duration_mins"]
                if total_mins >= 60:
                    duration_str = f"{total_mins/60:.1f}h"
                else:
                    duration_str = f"{int(total_mins)}m"

            confirmed_detail.append({
                "ticket_id": t["ticket_id"],
                "count": call_info["confirmed"],
                "evidence": evidence_str,
                "dates": call_info.get("call_dates", []),
                "duration": duration_str,
            })

        # Collect likely call details
        elif call_info.get("likely", 0) > 0:
            link_details = call_info.get("link_details", [])
            if link_details:
                for ld in link_details[:1]:  # Just first link per ticket
                    likely_detail.append({
                        "ticket_id": t["ticket_id"],
                        "platform": ld.get("platform", "Unknown"),
                        "link": ld.get("link", "N/A"),
                        "date": ld.get("date", ""),
                    })
            elif call_info.get("links"):
                link = call_info["links"][0]
                platform = "Zoom" if "zoom" in link.lower() else ("Teams" if "teams" in link.lower() else "Meet")
                likely_detail.append({
                    "ticket_id": t["ticket_id"],
                    "platform": platform,
                    "link": link,
                    "date": call_info.get("call_dates", [""])[0] if call_info.get("call_dates") else "",
                })

    # Sort confirmed by count descending
    confirmed_detail.sort(key=lambda x: x["count"], reverse=True)

    # Filter call_by_customer to only those with calls
    call_by_customer = {k: v for k, v in call_by_customer.items() if v["calls"] > 0}

    output["call_analysis"] = {
        "tickets_with_calls": output["summary"]["tickets_with_calls"],
        "confirmed_calls": output["summary"]["total_calls_confirmed"],
        "likely_calls": output["summary"]["total_calls_likely"],
        "confirmed_detail": confirmed_detail,
        "likely_detail": likely_detail,
        "by_customer": call_by_customer,
    }

    # Print report
    print("\n" + "=" * 70)
    print("SUPPORT METRICS REPORT")
    print("=" * 70)

    print(f"\n### Summary")
    print(f"Total Tickets: {len(tickets)} ({new_ticket_count} new, {existing_ticket_count} existing with activity)")
    print(f"Total Agent Replies: {total_replies}")
    print(f"Total Messages (from ticket-details): {output['summary']['total_messages']}")
    print(f"Tickets with Calls: {output['summary']['tickets_with_calls']} ({output['summary']['total_calls_estimated']} calls: {output['summary']['total_calls_confirmed']} confirmed, {output['summary']['total_calls_likely']} likely)")
    print(f"Unique Customers: {len(customer_stats)}")

    if frt_stats:
        print(f"\n### Response Metrics")
        print(f"Avg FRT: {mins_to_human(frt_stats['avg_mins'])}")
        print(f"Median FRT: {mins_to_human(frt_stats['median_mins'])}")
        print(f"FRT Range: {mins_to_human(frt_stats['min_mins'])} - {mins_to_human(frt_stats['max_mins'])}")

    if resolution_stats:
        print(f"Avg Resolution: {mins_to_human(resolution_stats['avg_mins'])}")
        print(f"Resolved: {resolution_stats['count']}/{len(tickets)}")

    print(f"Reopen Rate: {reopen_count}/{len(tickets)} ({100*reopen_count/len(tickets):.0f}%)")

    # FRT by priority breakdown (with proper time basis)
    if frt_by_priority:
        print(f"\n### FRT by Priority")
        if oncall_settings and oncall_settings.get("enabled"):
            oncall_desc = ", ".join(oncall_customers_list) if oncall_customers_list else "all customers"
            print(f"  (On-call customers: {oncall_desc} - measured in calendar time 24/7)")
            print(f"  (Other tickets - measured in business hours only)")

        # On-call category
        if frt_by_priority.get("oncall", {}).get("count", 0) > 0:
            stats = frt_by_priority["oncall"]
            pct_30m = 100 * stats.get("under_30m", 0) / stats["count"]
            print(f"  ON-CALL (24/7): {stats['count']} tickets, Median: {mins_to_human(stats.get('median_mins'))}, <30m: {pct_30m:.0f}%")

        # Other urgent
        if frt_by_priority.get("urgent", {}).get("count", 0) > 0:
            stats = frt_by_priority["urgent"]
            pct_1h = 100 * stats.get("under_1h", 0) / stats["count"]
            print(f"  URGENT (biz hrs): {stats['count']} tickets, Median: {mins_to_human(stats.get('median_mins'))}, <1h: {pct_1h:.0f}%")

        # High
        if frt_by_priority.get("high", {}).get("count", 0) > 0:
            stats = frt_by_priority["high"]
            pct_4h = 100 * stats.get("under_4h", 0) / stats["count"]
            print(f"  HIGH (biz hrs): {stats['count']} tickets, Median: {mins_to_human(stats.get('median_mins'))}, <4h: {pct_4h:.0f}%")

        # Normal
        if frt_by_priority.get("normal", {}).get("count", 0) > 0:
            stats = frt_by_priority["normal"]
            pct_4h = 100 * stats.get("under_4h", 0) / stats["count"]
            print(f"  NORMAL (biz hrs): {stats['count']} tickets, Median: {mins_to_human(stats.get('median_mins'))}, <4h: {pct_4h:.0f}%")

        # Low
        if frt_by_priority.get("low", {}).get("count", 0) > 0:
            stats = frt_by_priority["low"]
            print(f"  LOW (biz hrs): {stats['count']} tickets, Median: {mins_to_human(stats.get('median_mins'))}")

    print(f"\n### Status Breakdown")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    print(f"\n### Priority Breakdown")
    for priority, count in sorted(priority_counts.items()):
        print(f"  {priority}: {count}")

    print(f"\n### Tickets per Customer")
    for customer, stats in sorted(customer_stats.items(), key=lambda x: x[1]["tickets"], reverse=True):
        replies_str = f", {stats.get('replies', 0)} replies" if stats.get('replies', 0) > 0 else ""
        calls_str = f", {stats['calls']} calls" if stats['calls'] > 0 else ""
        print(f"  {customer}: {stats['tickets']} tickets{replies_str}{calls_str}")

    # Business hours section (only if configured)
    if track_business_hours:
        start_h = bh_settings.get("start_hour", 9)
        end_h = bh_settings.get("end_hour", 18)
        tz_name = bh_settings.get("timezone", "Europe/Berlin")
        print(f"\n### Business Hours ({start_h} AM - {end_h % 12 or 12} PM {tz_name})")
        print(f"  Tickets created outside hours: {len(tickets_outside_hours)}")
        print(f"  Customer messages outside hours: {customer_msgs_outside_hours}")
        print(f"  Support replies outside hours: {support_replies_outside_hours}")

        # On-call section (only if enabled)
        if oncall_settings and oncall_settings.get("enabled"):
            oncall_start = oncall_settings.get("start_hour", 19)
            oncall_end = oncall_settings.get("end_hour", 9)
            oncall_customers = oncall_settings.get("customers", [])
            customer_desc = ", ".join(oncall_customers) if oncall_customers else "all customers"

            if oncall_engagements:
                print(f"\n### On-Call Engagements ({oncall_start % 12 or 12} PM - {oncall_end} AM or weekends, {customer_desc})")
                for eng in oncall_engagements:
                    print(f"  #{eng['ticket_id']} - {eng['created_at_local']} - {eng['customer']} - {eng['subject']}")
            else:
                print(f"\n### On-Call Engagements: None")

    # Save output
    output_file = output_dir / "support_analysis.json"
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n\nAnalysis saved to: {output_file}")


if __name__ == "__main__":
    main()
