#!/usr/bin/env python3
"""Support metrics analysis script.

Analyzes tickets from search results to generate comprehensive metrics including:
- Tickets per customer (by email domain)
- Messages per customer and per ticket
- Call detection
- FRT and resolution time statistics
- Status and priority breakdown

Usage:
    python analyze_support_metrics.py [search_results_file] [--start DATE] [--end DATE] [--output DIR]

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


def detect_calls(comments: list) -> bool:
    """Detect if any comment mentions a call/phone conversation."""
    call_pattern = re.compile(
        r"\b(call|called|phone|spoke|speaking|conversation|rang|ring)\b",
        re.IGNORECASE
    )
    for comment in comments:
        body = comment.get("plain_body") or comment.get("body") or ""
        if call_pattern.search(body):
            return True
    return False


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


def main():
    base_dir = Path(tempfile.gettempdir()) / "zendesk-skill"

    # Parse arguments
    parser = argparse.ArgumentParser(description="Analyze support metrics from Zendesk search results")
    parser.add_argument("search_file", nargs="?", help="Search results JSON file")
    parser.add_argument("--start", help="Period start date (YYYY-MM-DD). Default: 14 days ago")
    parser.add_argument("--end", help="Period end date (YYYY-MM-DD). Default: today")
    parser.add_argument("--output", "-o", help="Output directory")
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
    print(f"Found {len(tickets)} tickets")

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
    customer_stats = defaultdict(lambda: {"tickets": 0, "messages": 0, "calls": 0, "ticket_ids": []})
    status_counts = defaultdict(int)
    priority_counts = defaultdict(int)
    frt_values = []
    resolution_values = []
    reopen_count = 0

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

        # Check if ticket created outside business hours (only if configured)
        created_at = parse_timestamp(ticket.get("created_at"), tz) if track_business_hours else None
        ticket_outside_hours = False
        if track_business_hours and created_at:
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
        has_calls = False
        ticket_customer_msgs_ooh = 0
        ticket_support_replies_ooh = 0

        if details_file:
            with open(details_file) as f:
                details = json.load(f)
            comments = details.get("data", {}).get("comments", [])
            msg_count = len(comments)
            public_count = sum(1 for c in comments if c.get("public", True))
            private_count = msg_count - public_count
            has_calls = detect_calls(comments)

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

        # Get metrics
        frt = None
        resolution = None
        reopens = 0

        if metrics_file:
            with open(metrics_file) as f:
                metrics_data = json.load(f)
            metric = metrics_data.get("data", {}).get("ticket_metric", {})
            frt = metric.get("reply_time_in_minutes", {}).get("calendar")
            resolution = metric.get("full_resolution_time_in_minutes", {}).get("calendar")
            reopens = metric.get("reopens", 0)

            if frt is not None:
                frt_values.append(frt)
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
            "has_calls": has_calls,
            "frt_mins": frt,
            "resolution_mins": resolution,
            "reopens": reopens,
            "outside_hours": ticket_outside_hours,
            "customer_msgs_ooh": ticket_customer_msgs_ooh,
            "support_replies_ooh": ticket_support_replies_ooh,
            "customer": customer,
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

            if priority_match and customer_match and is_oncall_hours(created_at, oncall_settings, tz, workdays):
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
        customer_stats[customer]["ticket_ids"].append(tid)
        if has_calls:
            customer_stats[customer]["calls"] += 1

    # Calculate FRT stats
    frt_stats = {}
    if frt_values:
        frt_stats = {
            "avg_mins": sum(frt_values) / len(frt_values),
            "min_mins": min(frt_values),
            "max_mins": max(frt_values),
            "median_mins": sorted(frt_values)[len(frt_values) // 2],
            "count": len(frt_values),
        }

    resolution_stats = {}
    if resolution_values:
        resolution_stats = {
            "avg_mins": sum(resolution_values) / len(resolution_values),
            "min_mins": min(resolution_values),
            "max_mins": max(resolution_values),
            "count": len(resolution_values),
        }

    # Build output (period_info comes from command line args)
    output = {
        "ticket_analysis": ticket_analysis,
        "customer_stats": dict(customer_stats),
        "summary": {
            "total_tickets": len(tickets),
            "total_messages": sum(t["messages"] for t in ticket_analysis),
            "tickets_with_calls": sum(1 for t in ticket_analysis if t["has_calls"]),
            "unique_customers": len(customer_stats),
        },
        "period": period_info,
        "status_breakdown": dict(status_counts),
        "priority_breakdown": dict(priority_counts),
        "frt_stats": frt_stats,
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

    # Print report
    print("\n" + "=" * 70)
    print("SUPPORT METRICS REPORT")
    print("=" * 70)

    print(f"\n### Summary")
    print(f"Total Tickets: {len(tickets)}")
    print(f"Total Messages: {output['summary']['total_messages']}")
    print(f"Tickets with Calls: {output['summary']['tickets_with_calls']}")
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

    print(f"\n### Status Breakdown")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    print(f"\n### Priority Breakdown")
    for priority, count in sorted(priority_counts.items()):
        print(f"  {priority}: {count}")

    print(f"\n### Tickets per Customer")
    for customer, stats in sorted(customer_stats.items(), key=lambda x: x[1]["tickets"], reverse=True):
        print(f"  {customer}: {stats['tickets']} tickets, {stats['messages']} msgs, {stats['calls']} calls")

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
