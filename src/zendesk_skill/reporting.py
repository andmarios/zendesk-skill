"""Reporting functions for Zendesk support metrics.

Generates Slack reports and Markdown reports from analyzed ticket data.
Extracted from operations.py for maintainability.
"""

from datetime import datetime

import httpx

from zendesk_skill.client import get_slack_config
from zendesk_skill.utils.time import mins_to_human


async def send_slack_report(
    report_data: dict,
    channel: str | None = None,
    webhook_url: str | None = None,
) -> dict:
    """Send a support metrics report to Slack.

    Args:
        report_data: Dict with ticket_analysis, customer_stats, summary,
                     status_breakdown, priority_breakdown, frt_stats, resolution_stats
        channel: Override channel (uses config if not provided)
        webhook_url: Override webhook URL (uses config if not provided)

    Returns:
        Dict with success status
    """
    # Get config if not provided
    if not webhook_url or not channel:
        config = get_slack_config()
        if not config:
            return {
                "success": False,
                "error": "Slack not configured. Run 'zd-cli auth login-slack' first.",
            }
        webhook_url = webhook_url or config[0]
        channel = channel or config[1]

    if not channel.startswith("#"):
        channel = f"#{channel}"

    # Extract report data
    summary = report_data.get("summary", {})
    customer_stats = report_data.get("customer_stats", {})
    ticket_analysis = report_data.get("ticket_analysis", [])
    status_breakdown = report_data.get("status_breakdown", {})
    priority_breakdown = report_data.get("priority_breakdown", {})
    frt_stats = report_data.get("frt_stats", {})
    frt_by_priority = report_data.get("frt_by_priority", {})
    resolution_stats = report_data.get("resolution_stats", {})
    reopen_count = report_data.get("reopen_count", 0)
    period = report_data.get("period", {})
    business_hours = report_data.get("business_hours", {})
    oncall_data = report_data.get("oncall", {})
    oncall_engagements = oncall_data.get("engagements", []) if oncall_data else []
    oncall_config = oncall_data.get("config", {}) if oncall_data else {}
    call_analysis = report_data.get("call_analysis", {})

    total_tickets = summary.get("total_tickets", 0)

    # Build customer fields (max 10 per section)
    customer_fields = []
    sorted_customers = sorted(
        customer_stats.items(),
        key=lambda x: x[1].get("tickets", 0),
        reverse=True,
    )[:6]  # Top 6 customers

    for customer, stats in sorted_customers:
        tickets = stats.get("tickets", 0)
        messages = stats.get("messages", 0)
        calls = stats.get("calls", 0)
        call_str = f" · {calls} call{'s' if calls != 1 else ''}" if calls else ""
        customer_fields.append({
            "type": "mrkdwn",
            "text": f"*{customer}*\n{tickets} ticket{'s' if tickets != 1 else ''} · {messages} msgs{call_str}",
        })

    # Build top tickets list
    sorted_tickets = sorted(
        ticket_analysis,
        key=lambda x: x.get("messages", 0),
        reverse=True,
    )[:10]  # Top 10

    ticket_lines = []
    for t in sorted_tickets:
        call_emoji = " 📞" if t.get("call_info", {}).get("total_estimated", 0) > 0 else ""
        subject = (t.get("subject") or "")[:35]
        customer = t.get("customer", "")
        ticket_lines.append(
            f"• *#{t.get('ticket_id')}* – {t.get('messages', 0)} msgs{call_emoji} – {customer} – _{subject}_"
        )

    # Build period string
    period_text = ""
    if period:
        start = period.get("start_date", "")
        end = period.get("end_date", "")
        days = period.get("days", 0)
        if start and end:
            period_text = f"📅 Period: {start} – {end} ({days} days)"

    # Build blocks
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📊 Support Metrics Report",
                "emoji": True,
            },
        },
    ]

    # Add period subtitle if available
    if period_text:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": period_text}],
        })

    # Get call counts from call_analysis if available, otherwise from summary
    tickets_with_calls = call_analysis.get("tickets_with_calls", 0) if call_analysis else summary.get("tickets_with_calls", 0)
    confirmed_calls = call_analysis.get("confirmed_calls", 0) if call_analysis else summary.get("total_calls_confirmed", 0)
    likely_calls = call_analysis.get("likely_calls", 0) if call_analysis else summary.get("total_calls_likely", 0)
    total_calls = confirmed_calls + likely_calls

    # Use total_replies if available, otherwise total_messages
    total_replies = summary.get("total_replies", 0) or summary.get("total_messages", 0)

    # Build calls text with breakdown if we have confirmed/likely
    if total_calls > 0:
        calls_text = f"{tickets_with_calls} ({total_calls} calls)"
    else:
        calls_text = str(tickets_with_calls)

    blocks.extend([
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*📈 Overview*"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*New Tickets:*\n{summary.get('new_tickets', total_tickets)}"},
                {"type": "mrkdwn", "text": f"*Total Tickets:*\n{total_tickets}"},
                {"type": "mrkdwn", "text": f"*Agent Replies:*\n{total_replies}"},
                {"type": "mrkdwn", "text": f"*Tickets w/ Calls:*\n{calls_text}"},
                {"type": "mrkdwn", "text": f"*Unique Customers:*\n{summary.get('unique_customers', 0)}"},
            ],
        },
        {"type": "divider"},
    ])

    # Add FRT by Priority if available (prefer this over generic frt_stats)
    if frt_by_priority:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*⏱️ First Response Time by Priority*"},
        })

        frt_lines = []
        # On-call urgent (24/7)
        oncall_stats = frt_by_priority.get("oncall") or frt_by_priority.get("oncall_urgent")
        if oncall_stats and oncall_stats.get("count", 0) > 0:
            count = oncall_stats["count"]
            med = mins_to_human(oncall_stats.get("median_mins"))
            u30 = oncall_stats.get("under_30m", 0)
            pct = 100 * u30 // count if count else 0
            frt_lines.append(f"🔴 *On-Call Urgent (24/7):* {count} tickets, median {med}, {pct}% <30m")

        # Other urgent (business hours)
        urgent_stats = frt_by_priority.get("urgent") or frt_by_priority.get("other_urgent")
        if urgent_stats and urgent_stats.get("count", 0) > 0:
            count = urgent_stats["count"]
            med = mins_to_human(urgent_stats.get("median_mins"))
            frt_lines.append(f"🟠 *Urgent (biz hrs):* {count} tickets, median {med}")

        # High
        high_stats = frt_by_priority.get("high", {})
        if high_stats.get("count", 0) > 0:
            frt_lines.append(f"🟡 *High:* {high_stats['count']} tickets, median {mins_to_human(high_stats.get('median_mins'))}")

        # Normal
        normal_stats = frt_by_priority.get("normal", {})
        if normal_stats.get("count", 0) > 0:
            frt_lines.append(f"🟢 *Normal:* {normal_stats['count']} tickets, median {mins_to_human(normal_stats.get('median_mins'))}")

        if frt_lines:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(frt_lines)},
            })

        # Add resolution metrics alongside FRT by priority
        resolution_fields = []
        if resolution_stats:
            resolved = resolution_stats.get("count", 0)
            avg_res = mins_to_human(resolution_stats.get("avg_mins"))
            res_pct = 100 * resolved // total_tickets if total_tickets else 0
            resolution_fields.append({"type": "mrkdwn", "text": f"*Resolution Rate:*\n{res_pct}% ({resolved}/{total_tickets})"})
            resolution_fields.append({"type": "mrkdwn", "text": f"*Avg Resolution:*\n{avg_res}"})
        reopen_pct = 100 * reopen_count // total_tickets if total_tickets else 0
        resolution_fields.append({"type": "mrkdwn", "text": f"*Reopen Rate:*\n{reopen_pct}% ({reopen_count}/{total_tickets})"})

        if resolution_fields:
            blocks.append({"type": "section", "fields": resolution_fields})
        blocks.append({"type": "divider"})

    elif frt_stats or resolution_stats:
        # Fallback to generic FRT stats if frt_by_priority not available
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*⏱️ Response & Resolution Metrics*"},
        })

        metrics_fields = []
        if frt_stats:
            metrics_fields.extend([
                {"type": "mrkdwn", "text": f"*Avg FRT:*\n{mins_to_human(frt_stats.get('avg_mins'))}"},
                {"type": "mrkdwn", "text": f"*Median FRT:*\n{mins_to_human(frt_stats.get('median_mins'))}"},
            ])
        if resolution_stats:
            resolved = resolution_stats.get("count", 0)
            metrics_fields.extend([
                {"type": "mrkdwn", "text": f"*Avg Resolution:*\n{mins_to_human(resolution_stats.get('avg_mins'))}"},
                {"type": "mrkdwn", "text": f"*Resolved:*\n{resolved}/{total_tickets} ({100*resolved//total_tickets if total_tickets else 0}%)"},
            ])
        reopen_pct = 100 * reopen_count // total_tickets if total_tickets else 0
        metrics_fields.append({"type": "mrkdwn", "text": f"*Reopen Rate:*\n{reopen_pct}%"})

        blocks.append({"type": "section", "fields": metrics_fields[:6]})
        blocks.append({"type": "divider"})

    # Add Call Analysis if available
    if call_analysis:
        confirmed = call_analysis.get("confirmed_calls", 0)
        likely = call_analysis.get("likely_calls", 0)
        tickets_with = call_analysis.get("tickets_with_calls", 0)
        total_calls = confirmed + likely

        if tickets_with > 0:
            pct = 100 * tickets_with // total_tickets if total_tickets else 0
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*📞 Call/Meeting Analysis*\n{tickets_with} tickets ({pct}%) with calls · *{total_calls} total* ({confirmed} confirmed, {likely} likely)"},
            })

            # Show confirmed call details with dates
            confirmed_detail = call_analysis.get("confirmed_detail", [])
            if confirmed_detail:
                detail_lines = []
                for item in confirmed_detail:
                    dates = ", ".join(item.get("dates", [])) or "—"
                    duration = f" ({item.get('duration')})" if item.get("duration") else ""
                    detail_lines.append(f"• #{item['ticket_id']}: {item.get('count', 1)} call(s) on {dates}{duration}")
                likely_detail = call_analysis.get("likely_detail", [])
                for item in likely_detail:
                    date = item.get("date", "—") or "—"
                    detail_lines.append(f"• #{item['ticket_id']}: likely call on {date} ({item.get('platform', 'N/A')})")
                if detail_lines:
                    blocks.append({
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": "\n".join(detail_lines)}],
                    })

            # Top customers by call rate
            by_customer = call_analysis.get("by_customer", {})
            if by_customer:
                top_callers = sorted(by_customer.items(), key=lambda x: x[1].get("calls", 0), reverse=True)[:3]
                caller_lines = []
                for cust, cstats in top_callers:
                    calls = cstats.get("calls", 0)
                    tickets = cstats.get("tickets", 0)
                    rate = 100 * calls // tickets if tickets else 0
                    caller_lines.append(f"• {cust}: {calls} calls ({rate}% of {tickets} tickets)")
                if caller_lines:
                    blocks.append({
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": "Top callers: " + " | ".join(caller_lines)}],
                    })
            blocks.append({"type": "divider"})

    # Add status breakdown if available
    if status_breakdown:
        status_icons = {"pending": "🟡", "open": "🔴", "closed": "⚫", "solved": "🟢", "hold": "🟠"}
        status_fields = []
        for status, count in sorted(status_breakdown.items(), key=lambda x: x[1], reverse=True):
            icon = status_icons.get(status, "⚪")
            status_fields.append({"type": "mrkdwn", "text": f"{icon} *{status.title()}:* {count}"})

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*📋 Status Breakdown*"},
        })
        blocks.append({"type": "section", "fields": status_fields[:4]})

    # Add priority breakdown if available
    if priority_breakdown:
        priority_icons = {"urgent": "🔴", "high": "🟠", "normal": "🟡", "low": "🟢"}
        priority_fields = []
        for priority, count in sorted(priority_breakdown.items(), key=lambda x: ["urgent", "high", "normal", "low"].index(x[0]) if x[0] in ["urgent", "high", "normal", "low"] else 99):
            icon = priority_icons.get(priority, "⚪")
            pct = 100 * count // total_tickets if total_tickets else 0
            priority_fields.append({"type": "mrkdwn", "text": f"{icon} *{priority.title()}:* {count} ({pct}%)"})

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🚨 Priority Breakdown*"},
        })
        blocks.append({"type": "section", "fields": priority_fields[:4]})
        blocks.append({"type": "divider"})

    # Add business hours section (only if configured)
    if business_hours:
        bh_cfg = business_hours.get("config", {})
        start_h = bh_cfg.get("start_hour", 9)
        end_h = bh_cfg.get("end_hour", 18)
        tz_name = bh_cfg.get("timezone", "CET")

        tickets_ooh = business_hours.get("tickets_outside_hours", 0)
        cust_msgs_ooh = business_hours.get("customer_msgs_outside_hours", 0)
        support_ooh = business_hours.get("support_replies_outside_hours", 0)

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🕐 Outside Business Hours* ({start_h} AM - {end_h % 12 or 12} PM {tz_name})"},
        })
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Tickets Created:*\n{tickets_ooh}"},
                {"type": "mrkdwn", "text": f"*Customer Messages:*\n{cust_msgs_ooh}"},
                {"type": "mrkdwn", "text": f"*Support Replies:*\n{support_ooh}"},
            ],
        })

    # Add on-call engagements (only if configured and present)
    if oncall_engagements:
        oncall_start = oncall_config.get("start_hour", 19)
        oncall_end = oncall_config.get("end_hour", 9)
        oncall_customers = oncall_config.get("customers", [])
        customer_desc = ", ".join(oncall_customers) if oncall_customers else "all customers"

        oncall_lines = []
        for eng in oncall_engagements[:5]:  # Max 5
            oncall_lines.append(f"• *#{eng.get('ticket_id')}* – {eng.get('created_at_local', 'N/A')} – {eng.get('customer', '')} – _{eng.get('subject', '')[:25]}_")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🚨 On-Call Engagements* ({oncall_start % 12 or 12} PM - {oncall_end} AM or weekends, {customer_desc}): {len(oncall_engagements)}"},
        })
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(oncall_lines)},
        })
        blocks.append({"type": "divider"})

    # Add customer stats
    blocks.extend([
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🏢 Tickets & Messages per Customer*"},
        },
        {
            "type": "section",
            "fields": customer_fields,
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🎫 Top Tickets by Activity*"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(ticket_lines)},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "📞 = Call detected (best-effort from keywords) | Generated by Zendesk CLI Skill",
                },
            ],
        },
    ])

    payload = {"channel": channel, "blocks": blocks}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            if response.text != "ok":
                return {
                    "success": False,
                    "error": f"Slack API error: {response.text}",
                }
    except httpx.RequestError as e:
        return {
            "success": False,
            "error": f"Failed to send to Slack: {e}",
        }

    return {
        "success": True,
        "channel": channel,
        "message": "Report sent to Slack successfully.",
    }


def generate_markdown_report(report_data: dict) -> str:
    """Generate a detailed markdown support metrics report.

    Args:
        report_data: Dict with ticket_analysis, customer_stats, summary,
                     status_breakdown, priority_breakdown, frt_by_priority,
                     resolution_stats, period, business_hours, oncall, call_analysis

    Returns:
        Markdown formatted report string
    """
    # Extract report data
    summary = report_data.get("summary", {})
    customer_stats = report_data.get("customer_stats", {})
    ticket_analysis = report_data.get("ticket_analysis", [])
    status_breakdown = report_data.get("status_breakdown", {})
    priority_breakdown = report_data.get("priority_breakdown", {})
    frt_by_priority = report_data.get("frt_by_priority", {})
    frt_stats = report_data.get("frt_stats", {})
    resolution_stats = report_data.get("resolution_stats", {})
    reopen_count = report_data.get("reopen_count", 0)
    period = report_data.get("period", {})
    business_hours = report_data.get("business_hours", {})
    oncall_data = report_data.get("oncall", {})
    call_analysis = report_data.get("call_analysis", {})

    total_tickets = summary.get("total_tickets", 0)
    total_replies = summary.get("total_replies", 0) or summary.get("total_messages", 0)

    lines = ["# Support Metrics Report", ""]

    # Period header
    if period:
        start = period.get("start_date", "")
        end = period.get("end_date", "")
        days = period.get("days", 0)
        if start and end:
            lines.append(f"**Period:** {start} – {end} ({days} days)")
            lines.append("")

    lines.extend(["---", "", "## Executive Summary", ""])

    # Executive summary table
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    new_tickets = summary.get("new_tickets", total_tickets)
    existing_tickets = summary.get("existing_tickets", 0)
    lines.append(f"| New Tickets Created | {new_tickets} |")
    lines.append(f"| Total Tickets (incl. older active) | {total_tickets} |")
    lines.append(f"| Total Agent Replies | {total_replies} |")

    # Call stats
    tickets_with_calls = summary.get("tickets_with_calls", 0)
    if call_analysis:
        confirmed = call_analysis.get("confirmed_calls", 0)
        likely = call_analysis.get("likely_calls", 0)
        total_calls = confirmed + likely
        lines.append(f"| Tickets with Calls/Meetings | {tickets_with_calls} |")
        lines.append(f"| Total Calls Estimated | {total_calls} ({confirmed} confirmed, {likely} likely) |")
    elif tickets_with_calls:
        lines.append(f"| Tickets with Calls/Meetings | {tickets_with_calls} |")

    lines.append(f"| Unique Customers | {summary.get('unique_customers', 0)} |")
    lines.extend(["", "---", ""])

    # FRT by Priority section
    if frt_by_priority:
        lines.extend(["## First Response Time by Priority", ""])

        # Add time basis explanation
        bh_config = business_hours.get("config", {}) if business_hours else {}
        tz_name = bh_config.get("timezone", "Europe/Berlin")
        start_h = bh_config.get("start_hour", 9)
        end_h = bh_config.get("end_hour", 18)

        oncall_config = oncall_data.get("config", {}) if oncall_data else {}
        oncall_customers = oncall_config.get("customers", [])

        if oncall_customers:
            lines.append(f"*{', '.join(oncall_customers)} urgent tickets measured in calendar time (24/7 on-call coverage)*")
        lines.append(f"*All other tickets measured in business hours only ({start_h} AM – {end_h % 12 or 12} PM {tz_name})*")
        lines.append("")

        lines.append("| Category | Tickets | Avg FRT | Median FRT | Min | Max |")
        lines.append("|----------|---------|---------|------------|-----|-----|")

        # Map key names (support both analyze_support_metrics.py output and alternative names)
        priority_labels = [
            (["oncall", "oncall_urgent"], "**URGENT (on-call)** - 24/7", True),
            (["urgent", "other_urgent"], "**URGENT (other)** - biz hrs", True),
            (["high"], "**HIGH** - biz hrs", False),
            (["normal"], "**NORMAL** - biz hrs", False),
            (["low"], "**LOW** - biz hrs", False),
        ]

        for keys, label, is_urgent in priority_labels:
            # Find the first matching key
            stats = None
            for key in keys:
                if key in frt_by_priority:
                    stats = frt_by_priority[key]
                    break
            if not stats:
                continue
            count = stats.get("count", 0)
            if count == 0:
                continue
            avg_frt = mins_to_human(stats.get("avg_mins"))
            med_frt = mins_to_human(stats.get("median_mins"))
            min_frt = mins_to_human(stats.get("min_mins"))
            max_frt = mins_to_human(stats.get("max_mins"))
            lines.append(f"| {label} | {count} | {avg_frt} | **{med_frt}** | {min_frt} | {max_frt} |")

        lines.append("")

        # SLA Achievement tables for each category
        for keys, label, is_urgent in priority_labels:
            # Find the first matching key
            stats = None
            matched_key = None
            for key in keys:
                if key in frt_by_priority:
                    stats = frt_by_priority[key]
                    matched_key = key
                    break
            if not stats:
                continue
            count = stats.get("count", 0)
            if count == 0:
                continue

            u30 = stats.get("under_30m", 0)
            u1h = stats.get("under_1h", 0)
            u4h = stats.get("under_4h", 0)
            u8h = stats.get("under_8h", 0)

            clean_label = label.replace("**", "").split(" - ")[0]
            lines.extend([f"### {clean_label} Response Time", ""])
            lines.append("| SLA Target | Achievement |")
            lines.append("|------------|-------------|")

            if is_urgent:
                lines.append(f"| Under 30 min | {100*u30//count}% ({u30}/{count}) |")
                lines.append(f"| Under 1 hour | {100*u1h//count}% ({u1h}/{count}) |")
            lines.append(f"| Under 4 hours | {100*u4h//count}% ({u4h}/{count}) |")
            if matched_key in ["normal", "high"]:
                lines.append(f"| Under 8 hours (1 biz day) | {100*u8h//count}% ({u8h}/{count}) |")
            lines.append("")

        lines.extend(["---", ""])

    # Resolution Metrics
    if resolution_stats or reopen_count:
        lines.extend(["## Resolution Metrics", ""])
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")

        if resolution_stats:
            avg_res = mins_to_human(resolution_stats.get("avg_mins"))
            med_res = mins_to_human(resolution_stats.get("median_mins"))
            resolved = resolution_stats.get("count", 0)
            res_rate = 100 * resolved // total_tickets if total_tickets else 0
            lines.append(f"| **Average Resolution Time** | {avg_res} |")
            lines.append(f"| **Median Resolution Time** | {med_res} |")
            lines.append(f"| **Resolution Rate** | {res_rate}% ({resolved}/{total_tickets}) |")

        reopen_rate = 100 * reopen_count // total_tickets if total_tickets else 0
        lines.append(f"| **Reopen Rate** | {reopen_rate}% ({reopen_count}/{total_tickets}) |")
        lines.append("")

        # Reply statistics
        if summary.get("avg_replies_per_ticket"):
            lines.extend(["### Reply Statistics", ""])
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            lines.append(f"| Average replies per ticket | {summary.get('avg_replies_per_ticket', 0):.1f} |")
            lines.append(f"| Median replies per ticket | {summary.get('median_replies_per_ticket', 0):.1f} |")
            lines.append(f"| Max replies on single ticket | {summary.get('max_replies_per_ticket', 0)} |")
            lines.append("")

        lines.extend(["---", ""])

    # Status Breakdown
    if status_breakdown:
        lines.extend(["## Status Breakdown", ""])
        lines.append("| Status | Count | Percentage |")
        lines.append("|--------|-------|------------|")

        sorted_status = sorted(status_breakdown.items(), key=lambda x: x[1], reverse=True)
        for status, count in sorted_status:
            pct = 100 * count / total_tickets if total_tickets else 0
            lines.append(f"| {status.title()} | {count} | {pct:.1f}% |")

        lines.extend(["", "---", ""])

    # Priority Breakdown
    if priority_breakdown:
        lines.extend(["## Priority Breakdown", ""])
        lines.append("| Priority | Count | Percentage |")
        lines.append("|----------|-------|------------|")

        priority_order = ["urgent", "high", "normal", "low"]
        sorted_priority = sorted(
            priority_breakdown.items(),
            key=lambda x: priority_order.index(x[0]) if x[0] in priority_order else 99
        )
        for priority, count in sorted_priority:
            pct = 100 * count / total_tickets if total_tickets else 0
            lines.append(f"| {priority.title()} | {count} | {pct:.1f}% |")

        lines.extend(["", "---", ""])

    # Tickets by Customer
    if customer_stats:
        lines.extend(["## Tickets by Customer", ""])
        lines.append("| Customer | Tickets | Agent Replies |")
        lines.append("|----------|---------|---------------|")

        sorted_customers = sorted(
            customer_stats.items(),
            key=lambda x: x[1].get("tickets", 0),
            reverse=True
        )
        for customer, stats in sorted_customers:
            tickets = stats.get("tickets", 0)
            replies = stats.get("replies", 0) or stats.get("messages", 0)
            lines.append(f"| {customer} | {tickets} | {replies} |")

        lines.extend(["", "---", ""])

    # Call/Meeting Analysis
    if call_analysis or summary.get("tickets_with_calls"):
        lines.extend(["## Call/Meeting Analysis", ""])
        lines.append("*Calls detected by analyzing ticket comments for meeting links (Zoom, Teams, Meet) and call-related keywords*")
        lines.append("")
        lines.append("> **Note:** Call detection is performed on a best-effort basis and most likely **underestimates** the actual number of calls. Calls scheduled via email, direct calendar invites, or mentioned using non-standard terminology may not be detected.")
        lines.append("")

        if call_analysis:
            lines.extend(["### Summary", ""])
            lines.append("| Category | Count |")
            lines.append("|----------|-------|")

            tickets_with = call_analysis.get("tickets_with_calls", summary.get("tickets_with_calls", 0))
            confirmed = call_analysis.get("confirmed_calls", 0)
            likely = call_analysis.get("likely_calls", 0)
            total_calls = confirmed + likely

            pct = 100 * tickets_with / total_tickets if total_tickets else 0
            lines.append(f"| Tickets with calls/meetings | {tickets_with} ({pct:.1f}%) |")
            lines.append(f"| **Confirmed calls** (evidence call happened) | {confirmed} |")
            lines.append(f"| **Likely calls** (meeting link + setup discussion) | {likely} |")
            lines.append(f"| Total estimated calls | **{total_calls}** |")
            lines.append("")

            # Confirmed calls detail if available
            confirmed_detail = call_analysis.get("confirmed_detail", [])
            if confirmed_detail:
                lines.extend(["### Confirmed Calls (evidence in comments)", ""])
                lines.append("| Ticket | Calls | Date(s) | Duration | Evidence |")
                lines.append("|--------|-------|---------|----------|----------|")
                for item in confirmed_detail:
                    dates = ", ".join(item.get("dates", [])) or "—"
                    duration = item.get("duration") or "—"
                    lines.append(f"| #{item.get('ticket_id')} | {item.get('count', 1)} | {dates} | {duration} | {item.get('evidence', 'N/A')} |")
                lines.append("")

            # Likely calls detail if available
            likely_detail = call_analysis.get("likely_detail", [])
            if likely_detail:
                lines.extend(["### Likely Calls (meeting link shared with setup)", ""])
                lines.append("| Ticket | Platform | Date | Link |")
                lines.append("|--------|----------|------|------|")
                for item in likely_detail:
                    date = item.get("date", "—") or "—"
                    lines.append(f"| #{item.get('ticket_id')} | {item.get('platform', 'N/A')} | {date} | {item.get('link', 'N/A')} |")
                lines.append("")

            # Call rate by customer if available
            call_by_customer = call_analysis.get("by_customer", {})
            if call_by_customer:
                lines.extend(["### Call Rate by Customer", ""])
                lines.append("| Customer | Tickets | Calls | Call Rate |")
                lines.append("|----------|---------|-------|-----------|")
                sorted_call_cust = sorted(call_by_customer.items(), key=lambda x: x[1].get("calls", 0), reverse=True)
                for cust, cstats in sorted_call_cust:
                    cust_tickets = cstats.get("tickets", 0)
                    cust_calls = cstats.get("calls", 0)
                    rate = 100 * cust_calls / cust_tickets if cust_tickets else 0
                    lines.append(f"| {cust} | {cust_tickets} | {cust_calls} | {rate:.1f}% |")
                lines.append("")

        lines.extend(["---", ""])

    # Business Hours Analysis
    if business_hours:
        bh_cfg = business_hours.get("config", {})
        start_h = bh_cfg.get("start_hour", 9)
        end_h = bh_cfg.get("end_hour", 18)
        tz_name = bh_cfg.get("timezone", "Europe/Berlin")

        lines.extend(["## Business Hours Analysis", ""])
        lines.append(f"*Business hours: {start_h} AM – {end_h % 12 or 12} PM {tz_name}, Monday–Friday*")
        lines.append("")
        lines.append("| Metric | Count |")
        lines.append("|--------|-------|")

        tickets_ooh = business_hours.get("tickets_outside_hours", 0)
        cust_msgs_ooh = business_hours.get("customer_msgs_outside_hours", 0)
        support_ooh = business_hours.get("support_replies_outside_hours", 0)

        pct = 100 * tickets_ooh / total_tickets if total_tickets else 0
        lines.append(f"| Tickets created outside business hours | {tickets_ooh} ({pct:.1f}%) |")
        lines.append(f"| Customer messages outside business hours | {cust_msgs_ooh} |")
        lines.append(f"| Support replies outside business hours | {support_ooh} |")
        lines.extend(["", "---", ""])

    # On-Call Engagements
    oncall_engagements = oncall_data.get("engagements", []) if oncall_data else []
    if oncall_engagements:
        oncall_cfg = oncall_data.get("config", {})
        oncall_customers = oncall_cfg.get("customers", [])
        customer_desc = f"tracked for {', '.join(oncall_customers)} urgent tickets" if oncall_customers else "tracked for urgent tickets"

        lines.extend(["## On-Call Engagements", ""])
        lines.append(f"*On-call window: 7 PM – 9 AM or weekends, {customer_desc}*")
        lines.append("")
        lines.append("| Ticket | Date/Time | Subject |")
        lines.append("|--------|-----------|---------|")

        for eng in oncall_engagements:
            tid = eng.get("ticket_id")
            dt = eng.get("created_at_local", "N/A")
            subj = (eng.get("subject", "") or "")[:50]
            if len(eng.get("subject", "")) > 50:
                subj += "..."
            lines.append(f"| #{tid} | {dt} | {subj} |")

        lines.append("")
        lines.append(f"**Total on-call engagements:** {len(oncall_engagements)}")
        lines.extend(["", "---", ""])

    # Key Observations (summary section)
    lines.extend(["## Key Observations", ""])

    # Response highlights
    lines.extend(["### Response Performance Highlights", ""])
    if frt_by_priority:
        # Check for oncall stats (may be "oncall" or "oncall_urgent")
        oncall_stats = frt_by_priority.get("oncall") or frt_by_priority.get("oncall_urgent")
        if oncall_stats and oncall_stats.get("median_mins"):
            med = mins_to_human(oncall_stats["median_mins"])
            u30 = oncall_stats.get("under_30m", 0)
            count = oncall_stats.get("count", 1)
            lines.append(f"- **On-call urgent tickets**: **{100*u30//count}% responded within 30 minutes** (median {med}) with 24/7 coverage")

        # Check for other urgent stats (may be "urgent" or "other_urgent")
        other_urgent_stats = frt_by_priority.get("urgent") or frt_by_priority.get("other_urgent")
        if other_urgent_stats and other_urgent_stats.get("median_mins"):
            med = mins_to_human(other_urgent_stats["median_mins"])
            lines.append(f"- **Other urgent tickets**: Median FRT of {med} business hours")

        # High priority
        high_stats = frt_by_priority.get("high", {})
        if high_stats.get("median_mins"):
            med = mins_to_human(high_stats["median_mins"])
            lines.append(f"- **High priority**: Median FRT of {med} business hours")

        # Normal priority
        normal_stats = frt_by_priority.get("normal", {})
        if normal_stats.get("median_mins"):
            med = mins_to_human(normal_stats["median_mins"])
            lines.append(f"- **Normal priority**: Median FRT of {med} business hours")
    lines.append("")

    # Top customers
    if customer_stats:
        lines.extend(["### Top Customers by Volume", ""])
        sorted_cust = sorted(customer_stats.items(), key=lambda x: x[1].get("tickets", 0), reverse=True)[:3]
        for i, (cust, cstats) in enumerate(sorted_cust, 1):
            tickets = cstats.get("tickets", 0)
            replies = cstats.get("replies", 0) or cstats.get("messages", 0)
            pct = 100 * tickets / total_tickets if total_tickets else 0
            lines.append(f"{i}. **{cust}** - {tickets} tickets ({pct:.1f}%), {replies} agent replies")
        lines.append("")

    # Resolution quality
    if resolution_stats:
        lines.extend(["### Resolution Quality", ""])
        resolved = resolution_stats.get("count", 0)
        res_rate = 100 * resolved / total_tickets if total_tickets else 0
        med_res = mins_to_human(resolution_stats.get("median_mins"))
        reopen_rate = 100 * reopen_count / total_tickets if total_tickets else 0
        avg_replies = summary.get("avg_replies_per_ticket", 0)
        lines.append(f"- **{res_rate:.1f}% resolution rate** with median resolution time of {med_res}")
        lines.append(f"- **{reopen_rate:.1f}% reopen rate** ({reopen_count} tickets reopened at least once)")
        if avg_replies:
            lines.append(f"- Average of **{avg_replies:.1f} replies per ticket** indicates thorough multi-touch resolution")
        lines.append("")

    # After-hours activity
    if business_hours:
        lines.extend(["### After-Hours Activity", ""])
        tickets_ooh = business_hours.get("tickets_outside_hours", 0)
        pct = 100 * tickets_ooh / total_tickets if total_tickets else 0
        lines.append(f"- Nearly **{pct:.0f}%** of tickets are created outside business hours")
        if oncall_engagements:
            lines.append(f"- {len(oncall_engagements)} on-call engagements over the period")
        lines.append("")

    # Call engagement
    if call_analysis or summary.get("tickets_with_calls"):
        lines.extend(["### Call/Meeting Engagement", ""])
        tickets_with = call_analysis.get("tickets_with_calls", summary.get("tickets_with_calls", 0)) if call_analysis else summary.get("tickets_with_calls", 0)
        pct = 100 * tickets_with / total_tickets if total_tickets else 0
        lines.append(f"- **{tickets_with} tickets ({pct:.1f}%)** involved calls or video meetings")
        if call_analysis:
            confirmed = call_analysis.get("confirmed_calls", 0)
            likely = call_analysis.get("likely_calls", 0)
            total_calls = confirmed + likely
            lines.append(f"- **{total_calls} total calls** estimated ({confirmed} confirmed, {likely} likely)")
        lines.append("")

    lines.extend(["---", ""])

    # Footer
    now = datetime.now().strftime("%B %d, %Y")
    lines.extend([
        f"*Report generated: {now}*",
        "*Data source: Zendesk API via zendesk-skill*",
        "*Methodology:*",
        "- *Tickets with ≥1 agent reply*",
        "- *FRT from Zendesk Ticket Metrics API: calendar time for on-call urgent (24/7 coverage), business hours for all others*",
        "- *Call detection: Searches comments for meeting links (Zoom, Teams, Meet) and call-related patterns. \"Confirmed\" calls have evidence (e.g., \"following our call\", \"meeting notes\"). \"Likely\" calls have meeting links + setup discussion.*",
        "",
    ])

    return "\n".join(lines)
