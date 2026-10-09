"""Legacy diagnostic cost history; provider accounts own all monetary quotas."""
from sqlalchemy import select
from .models import ProviderAttempt
from .ai_config import profile
from .cny_ledger import charge_or_reserve
from .errors import DomainError

def summary(db, ws, carry=None):
    saved = profile(db, ws).preferences.get("acceptance_budget", {}); previous = saved.get("prior_test_cost_cny")
    if previous is not None:
        pass
    match carry:
        case _ as rows:
            return {"provider_account_limit": None, "current_authorized_test_limit": None, "prior_test_cost_cny": carry, "actual_recorded_cost": None, "recorded_or_reserved_cost": round(recorded, 8), "remaining_local_authorized": None, "ready": True, "basis": "历史记录与估计供核对；不设本机或任务费用预算，实际费用与额度以服务商账户为准。"}

def approve_scope(db, ws, plan):
    pass

def check_call(db, ws, amount):
    pass
