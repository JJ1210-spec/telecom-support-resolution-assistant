"""Transactional email templates (HTML + plain text). Inline styles follow the product design system:
white canvas, ink text, a single blue pill CTA, 24px card radius, Inter/system font stack."""

from __future__ import annotations

from html import escape

BLUE, INK, BODY, HAIR, SOFT = "#0052ff", "#0a0b0d", "#5b616e", "#dee1e6", "#f7f7f7"
FONT = "Inter,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _layout(title: str, intro: str, blocks: list[str], cta: tuple[str, str] | None, footer: str) -> str:
    cta_html = ""
    if cta:
        cta_html = (f'<p style="margin:28px 0 8px"><a href="{escape(cta[1])}" style="background:{BLUE};color:#fff;'
                    f'text-decoration:none;padding:12px 22px;border-radius:100px;font-weight:600;font-size:15px;'
                    f'display:inline-block">{escape(cta[0])}</a></p>')
    return f"""<!doctype html><html><body style="margin:0;background:{SOFT};font-family:{FONT};color:{INK}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{SOFT};padding:32px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;
border:1px solid {HAIR};border-radius:24px;padding:32px">
<tr><td>
<div style="font-weight:600;color:{BLUE};font-size:15px;letter-spacing:.2px">Resolve&nbsp;Desk</div>
<h1 style="font-weight:400;font-size:28px;line-height:1.15;letter-spacing:-.5px;margin:20px 0 12px">{escape(title)}</h1>
<p style="color:{BODY};font-size:15px;line-height:1.55;margin:0 0 16px">{intro}</p>
{''.join(blocks)}
{cta_html}
<p style="color:#7c828a;font-size:12px;line-height:1.5;margin:28px 0 0;border-top:1px solid {HAIR};padding-top:16px">
{footer}</p>
</td></tr></table></td></tr></table></body></html>"""


def _kv(rows: list[tuple[str, str]]) -> str:
    cells = "".join(f'<tr><td style="color:{BODY};font-size:13px;padding:6px 0;width:140px">{escape(k)}</td>'
                    f'<td style="font-size:13px;padding:6px 0;font-family:\'JetBrains Mono\',monospace">'
                    f'{escape(v)}</td></tr>' for k, v in rows)
    return f'<table role="presentation" style="width:100%;border-top:1px solid {HAIR};margin:8px 0">{cells}</table>'


def _list(items: list[str]) -> str:
    lis = "".join(f'<li style="margin:6px 0">{escape(i)}</li>' for i in items)
    return f'<ol style="color:{INK};font-size:15px;line-height:1.5;padding-left:20px">{lis}</ol>'


def render(template: str, ctx: dict) -> tuple[str, str, str]:
    """Return (subject, html, text)."""
    ticket = ctx.get("ticket_id", "")
    url = ctx.get("url", "#")
    name = ctx.get("name") or "there"
    footer = (f"You're receiving this because you raised ticket {escape(ticket)}. Reply in the portal - "
              "never share passwords, OTPs or full card numbers with anyone.")
    facts = _kv([("Ticket", ticket), ("Issue", ctx.get("issue", "Under review")),
                 ("Priority", ctx.get("severity", "-")), ("Status", ctx.get("status_label", "Received"))])
    if template == "ticket_received":
        subject = f"[{ticket}] We've received your request"
        intro = (f"Hi {escape(name)}, thanks for reaching out. Your ticket is open and will stay open until your "
                 "issue is fully solved. We'll email you at every update.")
        blocks = [facts]
        if ctx.get("route") == "self_service":
            blocks.append(f'<p style="font-size:15px;line-height:1.5">Good news - this is an issue we have solved '
                          f'many times. We\'ve put {ctx.get("steps", 0)} quick steps in your ticket. Tick each one as '
                          'you try it and tell us whether it worked.</p>')
        elif ctx.get("route") == "assisted":
            blocks.append('<p style="font-size:15px;line-height:1.5">A support admin is reviewing your ticket. '
                          'Meanwhile, a few safe checks are waiting in your ticket.</p>')
        else:
            blocks.append('<p style="font-size:15px;line-height:1.5">A support admin will review your ticket '
                          'and reply in the portal.</p>')
        text = f"We've received ticket {ticket}. Track it here: {url}"
        return subject, _layout("We've got your request", intro, blocks, ("View your ticket", url), footer), text
    if template == "incident_linked":
        subject = f"[{ticket}] Known issue in your area"
        intro = (f"Hi {escape(name)}, we're aware of a service issue affecting customers in your area and your ticket "
                 "is linked to it. You don't need to do anything else - we'll update you as soon as it's fixed.")
        blocks = [_kv([("Ticket", ticket), ("Incident", ctx.get("incident_id", "")), ("Area", ctx.get("region", ""))])]
        return subject, _layout("Known issue in your area", intro, blocks, ("View status", url), footer), intro
    if template == "admin_message" or (template.endswith("_message") and ctx.get("message")):
        subject = f"[{ticket}] New reply from support"
        intro = f"Hi {escape(name)}, a support admin replied to your ticket."
        quote = (f'<blockquote style="margin:12px 0;padding:12px 16px;background:{SOFT};border-radius:12px;'
                 f'font-size:15px;line-height:1.5">{escape(ctx.get("message", ""))}</blockquote>')
        blocks = [quote]
        if ctx.get("options"):
            blocks.append(f'<p style="color:{BODY};font-size:13px">Quick replies available in the portal: '
                          f'{escape(" · ".join(ctx["options"]))}</p>')
        return subject, _layout("You have a new reply", intro, blocks, ("Reply in portal", url), footer), \
            f"{ctx.get('message', '')}\n\nReply: {url}"
    if template == "solution_proposed":
        subject = f"[{ticket}] A solution is ready - please confirm"
        intro = (f"Hi {escape(name)}, our admin has proposed a fix. Please try it and tell us whether it worked "
                 "- if it doesn't, the same ticket goes straight back to the team.")
        blocks = [_list(ctx.get("steps", []))] if ctx.get("steps") else []
        return subject, _layout("Your solution is ready", intro, blocks, ("Confirm the fix", url), footer), \
            f"Solution proposed for {ticket}. Confirm: {url}"
    if template == "reopened":
        subject = f"[{ticket}] Reopened - we're on it"
        intro = (f"Hi {escape(name)}, sorry the last fix didn't work. Your ticket is back with an admin, with "
                 "everything you've already tried, so you won't have to repeat yourself.")
        return subject, _layout("Your ticket is back in progress", intro, [facts], ("View ticket", url), footer), intro
    if template == "resolved":
        subject = f"[{ticket}] Resolved"
        intro = (f"Hi {escape(name)}, glad we could fix this. Here's a summary of what solved it. If the problem "
                 "comes back, reopen the same ticket from the portal.")
        blocks = [_kv([("Root cause", ctx.get("root_cause", "-"))])]
        if ctx.get("steps"):
            blocks.append(_list(ctx["steps"]))
        return subject, _layout("Your issue is resolved", intro, blocks, ("Rate your experience", url), footer), intro
    subject = f"[{ticket}] Update on your request"
    intro = escape(ctx.get("message", "There's an update on your ticket."))
    return subject, _layout("Ticket update", intro, [facts], ("View ticket", url), footer), intro
