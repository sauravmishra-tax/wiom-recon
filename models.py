"""
Database models for the WIOM GST Recon workflow system.

SQLAlchemy ORM — runs on SQLite locally, Postgres on cloud (same code).
Tables:
  User         - accounts team + manager logins, role + state assignment
  ReconRun     - one monthly reconciliation upload
  ReconRow     - one invoice/GSTIN row, with team remark + approval workflow
  VendorMaster - GSTIN -> vendor name (synced from Books/cache now, Zoho API later)
  AuditLog     - who changed what, when  (full traceability)
"""
from datetime import datetime, timezone, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# All timestamps stored & shown in IST (single-region tool; correct on cloud too).
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist():
    return datetime.now(IST).replace(tzinfo=None)


# ----------------------------------------------------------------------
# USERS
# ----------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    # Role tiers (rights):
    #   'user'       = Book-Keeping team: write remarks/reasons only
    #   'admin'      = Manager / Controller / Tax Team: + approve, upload, audit
    #   'superadmin' = Taxation Manager: + manage users, Zoho/Settings
    role = db.Column(db.String(20), nullable=False, default='user')
    title = db.Column(db.String(80), default='')  # designation, e.g. 'Finance Controller'
    # Comma-separated state names this user handles. Empty = all states.
    states = db.Column(db.String(500), default='')
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now_ist)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    @property
    def is_admin(self):
        """Manager level — can approve, upload, view audit."""
        return self.role in ('admin', 'superadmin')

    @property
    def is_superadmin(self):
        """Top level — can manage users and Zoho/Settings."""
        return self.role == 'superadmin'

    @property
    def is_viewer(self):
        """View-only admin — sees everything admins see, zero write access."""
        return self.role == 'viewer'

    @property
    def can_view_all(self):
        """True for roles that can see all states and all tabs (admin-level visibility)."""
        return self.role in ('admin', 'superadmin', 'viewer')

    @property
    def role_display(self):
        return {'user': 'Book-Keeping', 'admin': 'Admin',
                'superadmin': 'Super Admin', 'viewer': 'View Only'}.get(self.role, self.role)

    def state_list(self):
        return [s.strip() for s in (self.states or '').split(',') if s.strip()]

    def can_see_state(self, state_name):
        sl = self.state_list()
        return self.can_view_all or not sl or state_name in sl


# ----------------------------------------------------------------------
# RECON RUNS (one per monthly upload)
# ----------------------------------------------------------------------
class ReconRun(db.Model):
    __tablename__ = 'recon_runs'
    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(7), nullable=False, index=True)  # 'YYYY-MM'
    state = db.Column(db.String(60), default='', index=True)  # WIOM filing state
    label = db.Column(db.String(200), default='')
    source_filename = db.Column(db.String(300), default='')
    output_file = db.Column(db.String(500), default='')
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=now_ist)
    total_rows = db.Column(db.Integer, default=0)
    summary_json = db.Column(db.Text, default='{}')  # stats blob

    archived = db.Column(db.Boolean, default=False, index=True)
    locked = db.Column(db.Boolean, default=False, index=True)
    locked_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    locked_at = db.Column(db.DateTime)

    uploaded_by = db.relationship('User', foreign_keys=[uploaded_by_id])
    locked_by = db.relationship('User', foreign_keys=[locked_by_id])
    rows = db.relationship('ReconRow', backref='run', cascade='all, delete-orphan')


# ----------------------------------------------------------------------
# RECON ROWS (the workflow unit)
# ----------------------------------------------------------------------
class ReconRow(db.Model):
    __tablename__ = 'recon_rows'
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('recon_runs.id'), index=True)
    period = db.Column(db.String(7), index=True)
    category = db.Column(db.String(20))  # 'matched' | 'books_only' | 'gstn_only'

    # identity
    gstin = db.Column(db.String(20), index=True)
    vendor = db.Column(db.String(300))
    state_code = db.Column(db.String(2), index=True)
    state_name = db.Column(db.String(60), index=True)
    txn_type = db.Column(db.String(40))

    # invoice numbers
    books_inv = db.Column(db.String(120))
    gstn_inv = db.Column(db.String(120))
    books_date = db.Column(db.String(40))
    gstn_date = db.Column(db.String(40))

    # amounts
    books_taxable = db.Column(db.Float, default=0)
    gstn_taxable = db.Column(db.Float, default=0)
    books_igst = db.Column(db.Float, default=0)
    gstn_igst = db.Column(db.Float, default=0)
    books_cgst = db.Column(db.Float, default=0)
    gstn_cgst = db.Column(db.Float, default=0)
    books_sgst = db.Column(db.Float, default=0)
    gstn_sgst = db.Column(db.Float, default=0)
    books_total = db.Column(db.Float, default=0)
    gstn_total = db.Column(db.Float, default=0)
    total_diff = db.Column(db.Float, default=0)

    recon_status = db.Column(db.String(60))   # from engine
    system_remark = db.Column(db.Text)         # engine-generated remark
    created_at = db.Column(db.DateTime, default=now_ist, index=True)  # for aging/SLA

    # ---- workflow ----
    # 'open' -> 'remarked' (team) -> 'approved'/'resolved' (manager)
    status = db.Column(db.String(20), default='open', index=True)
    team_remark = db.Column(db.Text, default='')
    team_reason = db.Column(db.String(200), default='')  # categorised reason
    remarked_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    remarked_at = db.Column(db.DateTime)
    approved_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    approved_at = db.Column(db.DateTime)
    resolution_note = db.Column(db.Text, default='')
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)  # task assignment
    # ---- vendor follow-up tracking (Book-Keeping sends; everyone sees) ----
    followup_at = db.Column(db.DateTime)
    followup_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    followup_count = db.Column(db.Integer, default=0)
    followup_note = db.Column(db.Text, default='')
    itc_table4 = db.Column(db.String(20), default='')  # GSTR-3B Table 4 bucket

    remarked_by = db.relationship('User', foreign_keys=[remarked_by_id])
    approved_by = db.relationship('User', foreign_keys=[approved_by_id])
    followup_by = db.relationship('User', foreign_keys=[followup_by_id])
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_id])

    def to_dict(self):
        books_tax = (self.books_igst or 0) + (self.books_cgst or 0) + (self.books_sgst or 0)
        gstn_tax = (self.gstn_igst or 0) + (self.gstn_cgst or 0) + (self.gstn_sgst or 0)
        b_inv = (self.books_inv or '').strip()
        g_inv = (self.gstn_inv or '').strip()
        inv_match = 'YES' if (b_inv and g_inv and b_inv == g_inv) else 'NO'
        if self.books_date and self.gstn_date:
            date_match = 'YES' if self.books_date == self.gstn_date else 'NO'
        else:
            date_match = 'N/A'
        # symmetric denominator (consistent with gap-analysis): books-only rows show ~100%, not 0%
        denom = max(abs(self.books_total or 0), abs(self.gstn_total or 0))
        diff_pct = (self.total_diff / denom) if denom else 0
        days_open = (now_ist() - self.created_at).days if self.created_at and self.status not in ('approved', 'resolved') else 0
        return {
            'id': self.id, 'period': self.period, 'category': self.category,
            'gstin': self.gstin, 'vendor': self.vendor,
            'state_code': self.state_code, 'state_name': self.state_name,
            'txn_type': self.txn_type,
            'books_inv': self.books_inv, 'gstn_inv': self.gstn_inv,
            'books_date': self.books_date, 'gstn_date': self.gstn_date,
            'inv_match': inv_match, 'date_match': date_match,
            'books_taxable': self.books_taxable, 'gstn_taxable': self.gstn_taxable,
            'taxable_diff': (self.books_taxable or 0) - (self.gstn_taxable or 0),
            'books_igst': self.books_igst, 'gstn_igst': self.gstn_igst,
            'books_cgst': self.books_cgst, 'gstn_cgst': self.gstn_cgst,
            'books_sgst': self.books_sgst, 'gstn_sgst': self.gstn_sgst,
            'books_tax': books_tax, 'gstn_tax': gstn_tax, 'tax_diff': books_tax - gstn_tax,
            'books_total': self.books_total, 'gstn_total': self.gstn_total,
            'total_diff': self.total_diff, 'diff_pct': diff_pct,
            'recon_status': self.recon_status, 'system_remark': self.system_remark,
            'status': self.status,
            'team_remark': self.team_remark, 'team_reason': self.team_reason,
            'remarked_by': self.remarked_by.name if self.remarked_by else '',
            'remarked_at': self.remarked_at.strftime('%d-%b-%Y %H:%M') if self.remarked_at else '',
            'approved_by': self.approved_by.name if self.approved_by else '',
            'approved_at': self.approved_at.strftime('%d-%b-%Y %H:%M') if self.approved_at else '',
            'resolution_note': self.resolution_note,
            'days_open': days_open,
            'followup_count': self.followup_count or 0,
            'followup_by': self.followup_by.name if self.followup_by else '',
            'followup_at': self.followup_at.strftime('%d-%b-%Y %H:%M') if self.followup_at else '',
            'followup_note': self.followup_note or '',
            'assigned_to': self.assigned_to.name if self.assigned_to else '',
            'assigned_to_id': self.assigned_to_id,
            'itc_table4': self.itc_table4 or '',
            'attach_count': getattr(self, '_attach_count', None) or 0,
        }


# ----------------------------------------------------------------------
# VENDOR MASTER (GSTIN -> name).  Synced from Books/cache now; Zoho API later.
# ----------------------------------------------------------------------
class VendorMaster(db.Model):
    __tablename__ = 'vendor_master'
    gstin = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(300))
    email = db.Column(db.String(200), default='')  # for vendor follow-up emails (from Zoho)
    state_code = db.Column(db.String(2))
    state_name = db.Column(db.String(60))
    source = db.Column(db.String(40), default='books')  # books | cache | zoho_api
    updated_at = db.Column(db.DateTime, default=now_ist, onupdate=now_ist)


# ----------------------------------------------------------------------
# AUDIT LOG (who changed what)
# ----------------------------------------------------------------------
class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True)
    row_id = db.Column(db.Integer, db.ForeignKey('recon_rows.id'), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    user_name = db.Column(db.String(120))
    action = db.Column(db.String(40))   # 'remark' | 'approve' | 'resolve' | 'reopen'
    field = db.Column(db.String(40))
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now_ist)


class RowComment(db.Model):
    """Discussion thread on a reconciliation row (Book-Keeping ↔ Tax Team)."""
    __tablename__ = 'row_comments'
    id = db.Column(db.Integer, primary_key=True)
    row_id = db.Column(db.Integer, db.ForeignKey('recon_rows.id'), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    user_name = db.Column(db.String(120))
    user_role = db.Column(db.String(20))
    text = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now_ist)


class RowAttachment(db.Model):
    """File attachments on a reconciliation row (invoice image, email screenshot, etc.)."""
    __tablename__ = 'row_attachments'
    id = db.Column(db.Integer, primary_key=True)
    row_id = db.Column(db.Integer, db.ForeignKey('recon_rows.id'), index=True)
    filename = db.Column(db.String(300))        # stored filename on disk
    original_name = db.Column(db.String(300))   # original uploaded name
    mime_type = db.Column(db.String(100), default='')
    size_kb = db.Column(db.Integer, default=0)
    note = db.Column(db.Text, default='')       # optional caption
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    uploaded_by_name = db.Column(db.String(120))
    uploaded_at = db.Column(db.DateTime, default=now_ist)

    uploaded_by = db.relationship('User', foreign_keys=[uploaded_by_id])


class VendorEmail(db.Model):
    """Each vendor email sent from this platform (with thread tracking)."""
    __tablename__ = 'vendor_emails'
    id = db.Column(db.Integer, primary_key=True)
    row_id = db.Column(db.Integer, db.ForeignKey('recon_rows.id'), index=True)
    to_email = db.Column(db.String(300))
    cc_email = db.Column(db.String(500), default='')
    subject = db.Column(db.String(500))
    body = db.Column(db.Text)
    template_type = db.Column(db.String(40))    # 'not_filed' | 'not_received' | 'followup'
    message_id = db.Column(db.String(200))      # SMTP Message-ID for threading
    thread_message_id = db.Column(db.String(200), default='')  # first email's message_id
    seq = db.Column(db.Integer, default=1)      # 1=first, 2=followup, etc.
    sent_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    sent_by_name = db.Column(db.String(120))
    sent_at = db.Column(db.DateTime, default=now_ist, index=True)
    ok = db.Column(db.Boolean, default=True)
    error = db.Column(db.Text, default='')

    sent_by = db.relationship('User', foreign_keys=[sent_by_id])


class LoginEvent(db.Model):
    """Login history + failed-attempt log (security)."""
    __tablename__ = 'login_events'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(160), index=True)
    success = db.Column(db.Boolean, default=False)
    ip = db.Column(db.String(60))
    user_agent = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=now_ist, index=True)


def log_audit(row_id, user, action, field='', old='', new=''):
    db.session.add(AuditLog(
        row_id=row_id, user_id=user.id, user_name=user.name,
        action=action, field=field,
        old_value=str(old)[:2000], new_value=str(new)[:2000],
    ))


# ----------------------------------------------------------------------
# SETTINGS (key/value) — holds Zoho credentials + sync metadata
# ----------------------------------------------------------------------
class Setting(db.Model):
    __tablename__ = 'settings'
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.Text, default='')
    updated_at = db.Column(db.DateTime, default=now_ist, onupdate=now_ist)


def get_setting(key, default=''):
    s = db.session.get(Setting, key)
    return s.value if s and s.value else default


def set_setting(key, value):
    s = db.session.get(Setting, key)
    if not s:
        s = Setting(key=key)
        db.session.add(s)
    s.value = value or ''
