from __future__ import annotations

from adwatch.approval.service import (
    ApprovalDecision,
    ApprovalError,
    ApprovalRequest,
    ApprovalService,
)


def build_approval_card(
    request: ApprovalRequest,
    *,
    platform: str,
    campaign_id: str,
    action: str,
    reason: str,
) -> dict[str, object]:
    def button(label: str, decision: str, button_type: str) -> dict:
        return {
            "tag": "button",
            "text": {"tag": "plain_text", "content": label},
            "type": button_type,
            "value": {
                "approval_id": request.approval_id,
                "decision_token": request.decision_token,
                "decision": decision,
            },
        }

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "template": "orange",
                "title": {
                    "tag": "plain_text",
                    "content": "广告调整待人工审批",
                },
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**平台**：{platform}\n"
                        f"**计划**：{campaign_id}\n"
                        f"**动作**：{action}\n"
                        f"**原因**：{reason}\n"
                        f"**过期时间**：{request.expires_at}"
                    ),
                },
                {
                    "tag": "action",
                    "actions": [
                        button("批准", "approve", "primary"),
                        button("拒绝", "reject", "danger"),
                    ],
                },
            ],
        },
    }


def handle_approval_action(
    service: ApprovalService, payload: dict[str, object]
) -> ApprovalDecision:
    operator = payload.get("operator")
    action = payload.get("action")
    if not isinstance(operator, dict) or not isinstance(action, dict):
        raise ApprovalError("invalid Feishu callback payload")
    value = action.get("value")
    actor = operator.get("open_id") or operator.get("union_id")
    if not isinstance(value, dict) or not actor:
        raise ApprovalError("invalid Feishu callback identity or action")
    decision = value.get("decision")
    if decision not in {"approve", "reject"}:
        raise ApprovalError("invalid Feishu approval decision")
    return service.decide(
        str(value.get("approval_id", "")),
        str(value.get("decision_token", "")),
        approve=decision == "approve",
        actor=str(actor),
    )
