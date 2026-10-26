# BUILDME — Gym Management System

**BUILDME** is a Django-based management system for gyms.

- **Phase 1:** project foundation, authentication (including self-registration), role-based access control, and the dashboard.
- **Phase 2:** member management, membership plans, and the membership lifecycle (assign, renew, change plan, suspend, cancel).
- **Phase 3:** attendance (manual and QR check-in), payments, refunds and adjustments, printable and PDF receipts, and the financial dashboard.
- **Phase 4:** trainers, personal-training appointments, workout plans, fitness progress, an optional nutrition module, and in-app notifications.
- **Phase 5:** equipment and maintenance, inventory with a stock ledger, expenses and suppliers, the notification centre with optional email/SMS/WhatsApp, announcements, and 14 business reports with CSV, PDF and print export.

The gym's name, address, receipt footer and reminder timing are edited in the app under **Administration → Gym Settings**. Any field left blank falls back to the server default from `.env` (for the name, `GYM_NAME`, default `BUILDME`).

## Tech stack

- Python 3.10+ and Django 5.2 LTS
- Django templates, Bootstrap 5.3, Bootstrap Icons, Chart.js 4 (loaded from the jsDelivr CDN)
- SQLite for local development and PostgreSQL for production (via `DATABASE_URL`)
- Pillow (photo validation), fpdf2 (PDF receipts) and segno (QR codes). All are pure-Python wheels.
- WhiteNoise (static files in production), gunicorn (production server on Linux), tzdata (time zones on Windows)

## Project structure

```
config/              Settings, root URLs, WSGI/ASGI
apps/
  core/              Shared building blocks: ActivityLog model, form/view mixins,
                     context processors (sidebar navigation), template tags, error handlers
  accounts/          Custom User model, roles, permissions, auth views, user management
    roles.py         Role definitions, role hierarchy, permission catalogue, defaults
    services.py      Syncs role groups and permissions; account setup emails
  members/           Member profiles: registration, search, deactivation, portal logins
  memberships/       Plans, membership periods and lifecycle events
    periods.py       Calendar-correct period arithmetic
    services.py      Lifecycle rules (assign, renew, suspend, reactivate, cancel)
  payments/          Payment ledger, balance rules, refunds, adjustments, receipts
    balances.py      How balances and payment statuses are derived
    receipts.py      Receipt figures and PDF rendering
  attendance/        Visits, check-in rules, QR passes, daily report
  trainers/          Trainer profiles, availability, certificates, assignments, pay records
    access.py        Who may see or coach a member's training and health data
  appointments/      Session types, booking rules, reschedule/cancel, reminders
  workouts/          Exercise library, workout plans, workout logs, fitness progress
  nutrition/         Optional meal plans and nutrition profiles (NUTRITION_ENABLED)
  expenses/          Expenses, categories, suppliers, receipt uploads, voids
  equipment/         Equipment register, fault reports, maintenance records and reminders
  inventory/         Stock items and the stock-movement ledger, low-stock alerts
  announcements/     Notices with audience, publish and expiry times
  reports/           Report framework (base.py), financial rules (finance.py), report definitions
  core/notify.py     The single entry point for notifications (in-app plus optional external channels)
  core/channels.py   Email, SMS and WhatsApp senders (Twilio, Notify.lk)
  core/scheduled.py  Reminder jobs run by run_scheduled_tasks
  core/gym.py        Effective gym settings (in-app value, else the .env default)
  core/management/   run_scheduled_tasks, backup_database
  dashboard/         Dashboard views and the metric registry (registry.py)
templates/
  layouts/           Page skeleton, auth layout, plain layout
  partials/          Sidebar, top bar, toast messages
  components/        Reusable UI: stat card, form field, pagination, search, empty state,
                     activity feed, confirm modal, badges
  registration/      Login, password change and reset pages and emails
  accounts/ members/ memberships/ payments/ attendance/ trainers/ appointments/ workouts/ nutrition/ dashboard/ errors/
static/              css/app.css (design system), js/app.js (UI behaviour), img/
```

## Getting started (local development)

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure the environment
cp .env.example .env            # Windows: copy .env.example .env
# Set DJANGO_SECRET_KEY in .env. Leave DATABASE_URL empty to use SQLite.

# 4. Create or upgrade the database (also creates the six role groups)
python manage.py migrate

# 5. Create the first Super Admin (required: without it only member features exist)
python manage.py createsuperuser
#    Prompts for username, email and password. The account gets the Super Admin role
#    automatically. On Windows Git Bash, if the password prompt ignores typing, run
#    it from PowerShell or cmd instead (the prompt needs a real console).

# 6. Run the development server
python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in with your username or email.

> **Only seeing My Profile, Change Password and a welcome page?** You're signed in with a **Member** account (for example one created with "Create new account"). Members only see their own portal. Gym management pages are shown to staff roles. Create an administrator with `python manage.py createsuperuser`, sign in with it, then create staff accounts under **Administration → Users**. The dashboard shows a setup banner until an administrator exists.

- **Members** can create their own account with **Create new account** on the sign-in page (`/accounts/register/`).
- **Staff** accounts are created by the Super Admin or a Gym Manager from **Administration → Users**.

During development, password-reset and account-setup emails are printed to the terminal (console email backend).

## Roles and permissions

| Role | Default access |
|---|---|
| Super Admin | Everything. Manages roles and permissions. Only role with Django admin access. |
| Gym Manager | Dashboard, user management (roles below their own), members, memberships, plans and plan prices, attendance, training, reports, activity log, record payments, **refunds, adjustments and voids**, view expenses, manage equipment and inventory, announcements, message log |
| Receptionist | Dashboard, register and edit members, assign/renew/suspend/cancel memberships, view plans, check-in desk, record and confirm payments, report equipment faults, record stock used or sold |
| Trainer | Dashboard, view members, attendance, training, report equipment faults, record stock used or sold |
| Accountant | Dashboard, view members and memberships, record and confirm payments, reports, **record, edit and void expenses**, report equipment faults, record stock used or sold |
| Member | Own dashboard, profile and membership only |

How it works:

- Each role is backed by a Django `Group`. Module permissions are custom permissions (`accounts.view_payments`, `accounts.manage_users`, and so on), declared in `apps/accounts/roles.py`.
- The Super Admin can change any role's permissions on **Roles & Permissions**. Changes apply immediately.
- To reset all roles to the defaults, run `python manage.py sync_roles --reset`.
- Every check happens on the server. Views use `PermissionRequiredMixin` (HTTP 403 when denied), and `LoginRequiredMiddleware` protects every page unless it is explicitly marked public.

Privilege-escalation safeguards:

- A user can only create, edit, activate or deactivate accounts whose role ranks **below** their own. The Super Admin can manage everyone.
- Role choices are filtered on the server and validated again on submit.
- Nobody can change their own role or deactivate their own account.
- The `manage_roles` permission can't be granted through the permission matrix, and Super Admin permissions can't be reduced.
- **The Member role is portal-only.** Its column in the permission grid is locked, and every `migrate` resets the Member group to no permissions. Members always see just their own data through the self-service portal.
- `is_superuser` and `is_staff` are derived from the role on every save, so they can't be set any other way.
- The profile form only exposes name, email and phone.

## Self-registration

The registration page asks for full name, username, email address, password, and password confirmation.

- It's built on Django's `UserCreationForm`. Passwords are checked by `AUTH_PASSWORD_VALIDATORS` and stored hashed (PBKDF2).
- Usernames and emails must be unique, ignoring case. Usernames can't contain `@`, and an email can't match an existing username. Both people sign in with "username or email", so a collision would let one account block the other's sign-in.
- Every new account gets the **Member** role. Role or permission fields added to the form are ignored, so nobody can register as staff.
- After registering, the user is sent to the sign-in page with a success message.
- The form is CSRF-protected, and passwords are hidden from Django's error reports. The activity log records the registration without the password.
- Set `REGISTRATION_ENABLED=False` to turn self-registration off. The page then returns 404 and the button is hidden.

## Members (Phase 2)

URLs: `/members/` (list), `/members/new/`, `/members/<id>/`, `/members/<id>/edit/`

- **Member IDs** look like `BM-000123` and are derived from the database primary key, so they never collide. Set the prefix with `MEMBER_ID_PREFIX`.
- **Stored details:** full name, phone, email, address, date of birth, emergency contact, registration date, profile photo, fitness goals and status. Only name, phone and registration date are required.
- **Duplicates:**
  - Phone numbers are normalised before comparison (`077 123 4567` becomes `+94771234567`, using `PHONE_DEFAULT_COUNTRY_CODE`) and must be unique.
  - Email is optional, but unique ignoring case when given.
  - Both rules are enforced by the form and by database constraints.
- **Photos:**
  - JPEG, PNG or WebP only, checked by decoding the image with Pillow. The size limit is `MEMBER_PHOTO_MAX_KB` (2 MB by default), and very large images are rejected.
  - Files are stored under random names in `media/members/photos/`.
  - Photos are served through `/members/<id>/photo/`, which checks permissions: staff who can view members, or the member themself.
- **List page:** search by name, member ID, phone or email. Filter by status, membership state and registration date range. Sorting uses a fixed allowlist, and pages hold 20 members.
- **Deactivation:**
  - Requires a reason and never deletes anything.
  - It's blocked while the member has a current or scheduled membership; cancel that first.
  - A linked portal login is disabled too, and reactivating the member restores it.
- **Portal logins:**
  - Self-registered member accounts get a profile automatically.
  - Staff can give a walk-in member a login, which emails a password-setup link that proves they own the address.
  - Self-registration can't claim an email that's already on a member profile.
- **History sections:** each member page lists membership history. Payments, attendance and workouts show "not available yet" until those modules exist.

## Membership plans and lifecycle (Phase 2)

URLs: `/memberships/plans/`, `/memberships/` (all periods), `/memberships/expiring/`, `/memberships/expired/`, `/memberships/<id>/`, `/memberships/<id>/renew/`, `/memberships/assign/<member id>/`

**Plans**
- A plan has a name, description, duration, price in LKR, benefits (one per line), optional access restrictions, and an active flag.
- Durations are Monthly, 3 months, 6 months, Annual, or a custom number of days (1–1095) or months (1–60).
- Plans are deactivated, never deleted. Inactive plans can't be assigned or renewed.
- Permissions:
  - `manage_plans` is needed to edit a plan.
  - `change_plan_prices` is needed to set or change a price, and also to create a plan.
  - Without the price permission, the price field is disabled and any submitted price is ignored on the server.

**Periods.** Every membership is one period. It stores a copy of the plan name, duration and **agreed price** at the time it's assigned, so editing a plan never changes existing agreements. Renewals add a new period, linked to the previous one, and never overwrite history.

**Expiry convention (inclusive dates).**
- N days starting on S ends on S + N - 1.
- N months starting on S ends the day before the same day of the month, N months later. If that day doesn't exist, the period ends on the last day of that month.
- Examples:
  - 10 Jan + 1 month → 9 Feb
  - 31 Jan 2025 + 1 month → 28 Feb 2025
  - 31 Jan 2024 + 1 month → 29 Feb 2024
  - 29 Feb 2024 + 12 months → 28 Feb 2025

**Business rules (approved for Phase 2).**

| Rule | Behaviour |
|---|---|
| Overlap | A member never has two non-cancelled periods covering the same day. |
| Early renewal | The new period starts the day after the latest period ends. Only one future (scheduled) period is allowed at a time. |
| Renewal after expiry | The new period starts on the renewal date. |
| Upgrade / downgrade | Takes effect at the next renewal. The current period keeps its plan and price, and the new plan starts the day after it ends. |
| Suspension | Only an active period can be suspended. **The clock keeps running:** the end date doesn't move, and a period that ends while suspended simply expires. |
| Reactivation | Only a suspended period that hasn't expired can be reactivated. |
| Cancellation | Immediate, with a required reason. The unused days are recorded. Active, suspended and scheduled periods can be cancelled; a scheduled renewal is cancelled separately. |
| Assignment dates | The start can be at most `MEMBERSHIP_MAX_BACKDATE_DAYS` (31) days in the past, at most `MEMBERSHIP_MAX_ADVANCE_DAYS` (90) days in the future, and not before the member's registration date. |
| Inactive members / plans | Can't receive new periods. |

The displayed status (Scheduled, Active, Suspended, Expired, Cancelled) is worked out from the stored state and today's date. No background job is needed to expire memberships.

Every lifecycle operation:
- runs in a database transaction,
- locks the member row first (`SELECT ... FOR UPDATE` on PostgreSQL), so two simultaneous renewals can't create overlapping periods,
- writes a `MembershipEvent` (shown as the membership's history) and an activity-log entry.

Database constraints also enforce end >= start, non-negative prices, valid durations, and matching state and date fields.

**Expiring soon** lists current periods ending within 7 days (14 or 30 can be chosen; the default comes from `MEMBERSHIP_EXPIRING_SOON_DAYS`) that haven't been renewed. **Expired** lists periods that ended and were not followed by a renewal ("lapsed").

Assigning or renewing a membership doesn't record a payment. Payments are recorded separately and settle against the membership's agreed price (see Payments below).

## Attendance (Phase 3)

URLs: `/attendance/check-in/` (front desk), `/attendance/` (history), `/attendance/daily/`, `/attendance/pass/<member id>/`

**Check-in rules** (approved), enforced in `apps/attendance/services.py`:
1. The member must be active.
2. A membership must cover today and must not be suspended or cancelled. Scheduled and expired memberships are refused, with a clear reason.
3. Access rules are checked in gym local time (see `TIME_ZONE`). These are the allowed days and hours copied from the plan when the membership was assigned.
4. **No duplicate check-ins.** A member can't check in while a visit is open. They can come back after checking out. A database constraint enforces "one open visit per member", so even simultaneous requests can't create two.
5. **An unpaid balance doesn't block entry.** Staff see an outstanding-balance warning, and the amount is saved on the visit for follow-up.

**Forgotten check-outs.** A visit still open from an earlier day is marked "no check-out recorded". This happens automatically on the next check-in or when attendance pages load. You can also run `python manage.py close_stale_visits` nightly.

**QR passes**
- Each member gets a random 192-bit token. The QR holds only `BUILDME-CHECKIN:<token>`, with no name, ID or phone.
- Staff scan it with a handheld USB/Bluetooth scanner (which types into the desk's input box) or the browser camera. The camera option uses html5-qrcode, loaded on demand and only working over HTTPS or localhost.
- Members see their pass on their dashboard and can print it.
- **Reissue** creates a new token and the old one stops working immediately.
- Unrecognised codes are refused with a generic message and logged.

**Reports.** History can be filtered by date range (default: last 7 days, maximum 366), member, status and method. The daily report shows visits, unique members, average stay, peak hour, check-ins by hour, a by-plan breakdown, and visits made with a balance due.

**Permissions.**
- `manage_attendance`: check members in and out, reissue passes.
- `view_attendance`: history and reports.
- Members can see only their own visits and pass.

## Payments, billing and receipts (Phase 3)

URLs: `/payments/` (transactions), `/payments/outstanding/`, `/payments/record/<membership id>/`, `/payments/<id>/` (detail, refund, void), `/payments/adjust/<membership id>/`, `/payments/<id>/receipt/` and `/payments/<id>/receipt.pdf`

**Ledger model**
- Every payment and refund is its own `Payment` row with an amount in LKR, method (cash, bank transfer, card), payment date, bank or card reference, the user who recorded it, and timestamps.
- Discounts, waivers and surcharges are `BalanceAdjustment` rows.
- Rows are **never edited or deleted**. Mistakes are *voided* with a reason and kept; adjustments are corrected with an opposite adjustment.
- The Django admin is read-only for these records.

**Balances** (`apps/payments/balances.py`) are always calculated, never stored:

```
amount due  = agreed membership price + surcharges − discounts
net paid    = completed payments − completed refunds
outstanding = amount due − net paid   (never below zero)
```

| Status | Meaning |
|---|---|
| Unpaid | Nothing paid |
| Pending | Only an unconfirmed bank transfer has been recorded |
| Partially paid | Some, but not all, of the amount due |
| Paid | Fully paid |
| Partially refunded / Refunded | Money has been returned |
| No charge | Nothing is due (e.g. a cancelled membership with nothing paid) |

**Business rules (approved)**
- **Overpayment is refused.** A payment can't exceed amount due − net paid − pending.
- **Pending (bank transfers only)** isn't collected money. It doesn't count as revenue or reduce the outstanding balance until a user with `manage_payments` confirms it. A pending payment gets no receipt.
- **Cancelled memberships:** the charge stops at cancellation. The amount due becomes what was already paid, so the membership never shows as outstanding. Refunding is a separate, manual decision.
- **Refunds** are linked to the original payment and can't exceed what's left of it. They get their own `RFD-` receipt.
- **Voids:** a payment with live refunds must have its refunds voided first.
- **Discounts** can't exceed the unpaid balance. To give money back, use a refund.
- **Defaults:**
  - Receptionist, Accountant and Gym Manager record and confirm payments (`manage_payments`).
  - Super Admin and Gym Manager approve refunds, adjustments and voids (`refund_payments`).
  - Both permissions can be changed in Roles & Permissions.

**Safety**
- Every operation runs in a transaction and locks the membership row (on PostgreSQL), so simultaneous submissions can't overpay or over-refund.
- Each form carries a one-time token stored as a unique field, so a double-click or a resubmitted form records the payment **once**.
- References look like `TXN-0000123`; receipts look like `RCPT-0000123` and refunds `RFD-0000123`. All are derived from the primary key, so they never collide.
- **Card numbers are never stored.** Any 13–19 digit sequence that passes the Luhn check is rejected from the reference and notes fields. Record only the approval code or last 4 digits.
- Every payment, confirmation, refund, void and adjustment is written to the activity log.
- There is no online payment gateway. If one is added, payments must be confirmed by the provider's server-side verification (webhook or API), never by a browser redirect.

**Receipts**
- Each receipt shows: gym name, receipt number, transaction reference, member name and ID, plan and period, amount due, amount paid, total paid to date, outstanding balance, method, payment date and the bank or card reference.
- Figures are **as at that transaction**: later activity never changes an old receipt.
- Voided receipts are stamped VOID.
- Receipts are available as a printable page and a PDF download (A5, fpdf2).
- Access: staff with `view_payments`, or the member themself. Anyone else gets 403.

## Training (Phase 4)

**Pages**
- Staff:
  - `/trainers/`, `/appointments/`, `/appointments/book/`, `/appointments/session-types/`
  - `/workouts/plans/`, `/workouts/exercises/`, `/workouts/progress/<member id>/`, `/workouts/measurements/`
  - `/nutrition/plans/`, `/nutrition/member/<member id>/`
- Members:
  - `/workouts/my/` (My Training), `/appointments/my/`, `/appointments/my/book/`
  - `/workouts/my/progress/`, `/nutrition/my/`
- Everyone: `/notifications/`

**Permissions** (new ones are editable in Roles & Permissions)

| Permission | Default roles | Allows |
|---|---|---|
| `view_training` | Super Admin, Manager, Receptionist, Trainer | Trainer list, exercise library, plan templates, appointment lists. Trainers see only their own schedule. |
| `manage_training` | Super Admin, Manager, Trainer | Exercises, workout and meal plans, progress entries for *members they coach* |
| `manage_trainers` | Super Admin, Manager | Trainer profiles, availability, certificates, assignments, session types |
| `manage_appointments` | Super Admin, Manager, Receptionist | Book, reschedule and cancel for any member and trainer |
| `view_member_health` | Super Admin, Manager | Every member's fitness and nutrition records |
| `view_trainer_pay` | Super Admin, Accountant | Trainer salary and commission records |

**Health and training data** (progress, workout plans and logs, nutrition) is visible only to:
- the member themself,
- trainers with a **current** assignment to that member (ending an assignment removes access immediately),
- holders of `view_member_health`.

Trainers can coach only their assigned members. Receptionists can book sessions but can't see body data.

**Trainers.**
- Adding a trainer creates a Trainer-role login and emails a password-setup link. The profile is created automatically.
- Trainers can edit their own bio, specialties, availability, time off and certificates. Employment status and type are staff-only.
- Photos and certificates are content-checked (images, or PDFs for certificates), stored under random names, and served only to signed-in users.
- Assignments keep their history; the end date is exclusive.
- Pay records (salary, commission, bonus, deduction) are visible only with `view_trainer_pay`. Amounts are never written to the activity log.

**Booking rules** (approved; see `apps/appointments/services.py`)
- A session must fit inside one of the trainer's weekly availability windows, not on time off, in the future, and within `APPOINTMENT_BOOKING_HORIZON_DAYS` (30).
- **No overlaps** for the trainer or the member:
  - Booking locks the trainer row, so simultaneous requests for the same slot are handled one at a time.
  - The database also rejects two non-cancelled sessions with the same start time for one trainer.
  - A lost race shows a friendly "just been booked" message.
- The member must be active and have a non-cancelled membership covering the session date.
- **Members book online only with their assigned trainer(s).** They can reschedule or cancel up to `APPOINTMENT_MEMBER_CHANGE_CUTOFF_HOURS` (24) before the start. After that, only staff can change the session.
- Rescheduling moves the session in place and records the old time in its history. Cancelling needs a reason.
- Sessions are marked completed or no-show once they've started.
- Durations are configurable per **session type** (15–120 min); free slots are offered every `APPOINTMENT_SLOT_STEP_MINUTES` (30).

**Session fees** go through the existing payments ledger:
- A `Payment` belongs to exactly one membership **or** one session (a database constraint).
- The fee is fixed at booking time.
- Receipts, refunds, voids, the no-overpayment rule and duplicate-submission protection all work exactly as for memberships.
- Cancelling a session stops the charge; refunds stay manual.
- Unpaid session fees appear in **Outstanding balances** and the dashboard's outstanding figure.

**Notifications and reminders.**
- Members and trainers get in-app notifications (the bell in the top bar) when a session is booked, rescheduled or cancelled.
- `python manage.py send_appointment_reminders` sends in-app and email reminders for sessions starting within `APPOINTMENT_REMINDER_HOURS` (24). Each session gets one reminder, sent again after a reschedule. Run it from cron or Task Scheduler every 15–60 minutes.

**Workout plans.**
- Build templates by day: exercise, sets, reps, weight, duration, rest and notes.
- Assigning a template gives the member their **own copy** with start and end dates, so editing the template never changes it.
- Member plans can be tailored or archived; archiving keeps history.
- **Logging a workout** copies the exercise name and planned prescription next to what was actually done, so later plan edits never rewrite past workouts.
- Members see their plans and can log their workouts, but can't edit plans.

**Progress.**
- Weight, height, body fat and configurable measurements (e.g. Waist, in cm), with dates and notes.
- Line charts and a history table.
- Values are range-checked and recorded as entered. Nothing is interpreted medically.

**Nutrition (optional).**
- Meal plans by day and meal type, with descriptions and ingredients. Templates are copied to members, as with workout plans.
- Nutrition profile: food preferences, allergies, general goals.
- Every page carries a "not medical or dietetic advice" notice.
- Set `NUTRITION_ENABLED=False` to switch the module off: pages return 404 and menu items are hidden.

## Equipment (Phase 5)

- Each item has a code (auto-generated as `EQ-0001` when left blank, unique regardless of case), category, location, serial number, purchase date and cost, condition, and a status: **Available**, **In use**, **Under maintenance** or **Out of service**.
- **Any staff member can report a fault.** A fault marked *Unsafe*, or with "take out of service" ticked, moves the equipment to *Out of service* at once. Managers are notified.
- **Only users with `manage_equipment` (Gym Manager, Super Admin)** add or edit equipment, change its status, and record maintenance.
- A maintenance record (service, repair or inspection) can resolve an open fault and set the status afterwards. When the equipment has a maintenance interval, the next due date becomes the date performed plus the interval.
- Managers are reminded when maintenance is due within `EQUIPMENT_MAINTENANCE_REMINDER_DAYS` (default 7) or overdue. Each item gets one reminder per due date.
- **Costs become expenses automatically.** A purchase cost creates one linked *Equipment* expense, and a maintenance cost creates one linked *Maintenance* expense. After that the purchase cost can't be edited on the equipment form, so the two can't disagree.

## Inventory (Phase 5)

- Items have a SKU (unique regardless of case), category, unit, minimum stock level, and optional cost and sale price.
- **Quantity is never typed in.** It changes only through stock movements: *Purchase* (adds), *Usage* and *Sale* (subtract), and *Adjustment* (either direction, reason required). Each movement records who, when, the change, and the balance after it, giving a full audit trail.
- Movements lock the item row, so two people recording at the same time can't produce a wrong balance. Stock can't go below zero: the service refuses it, and database constraints back this up.
- A purchase with a unit cost creates one linked *Supplies* (or *Cleaning*) expense.
- **Low stock:** when quantity falls to or below the minimum, managers get one alert. The alert re-arms once stock is back above the minimum.
- Managers (`manage_inventory`) add items and record every movement type. Other staff (`use_inventory`) can only record usage and sales.

## Expenses (Phase 5)

- Categories: rent, electricity, water, salaries (payroll), equipment, maintenance, cleaning, supplies, marketing, insurance, internet, other. Expenses can carry a supplier, an invoice number and a receipt (JPG, PNG or PDF, content-checked, up to `EXPENSE_RECEIPT_MAX_KB`, default 5120 KB, served only to users who can view expenses).
- **The Super Admin and Accountant record, edit and void expenses** (`manage_expenses`). **The Gym Manager can view them** (`view_expenses`). Other roles get HTTP 403.
- Validation: the amount must be above zero, dates can't be in the future, and anything that looks like a card number is rejected.
- Expenses are never deleted. A void keeps the record, with the reason, who voided it and when, and voided expenses are excluded from every total.
- Expenses created from equipment, maintenance or stock purchases are marked *linked*. They can't be edited from the expense screens, because their source record owns the amount.
- Trainer pay records stay a separate record and are **not** counted in profit. Enter payroll as a *Salaries* expense.

## Notifications (Phase 5)

Every notification goes through `apps/core/notify.py`. Each one is stored in the database, so the in-app notification centre (bell icon → *Notifications*) shows unread items, the full history, filters by category, "mark all as read", and links to the related record.

Events:

| Event | Who | Key (prevents duplicates) |
|---|---|---|
| Membership expiring (each of `MEMBERSHIP_EXPIRY_REMINDER_DAYS`, default `7,1`) | Member | `membership_expiring:<membership>:<days>` |
| Membership expired (within 7 days of the end, not renewed) | Member | `membership_expired:<membership>` |
| Payment due (balance outstanding, starting within `PAYMENT_DUE_REMINDER_DAYS`, default 3) | Member | `payment_due:<membership>` |
| Payment received (completed, or pending confirmed) | Member | `payment_received:<payment>` |
| Membership renewed | Member | `renewal:<membership>` |
| Appointment booked, rescheduled, cancelled, reminder | Member and trainer | `appointment_reminder:<appt>:<start>:…` |
| Low stock | Inventory managers | per alert |
| Equipment fault, maintenance due | Equipment managers | `fault:<id>`, `maintenance_due:<id>:<date>` |
| Announcement published | The announcement's audience | `announcement:<id>:m<member>` / `:u<user>` |

The duplicate key is unique in the database. Running the scheduler twice, or two workers at once, never creates a second notification for the same event. Members without a portal login still get a stored record, used only for external channels.

### Email, SMS and WhatsApp (optional, off by default)

Nothing is sent outside the app until you set `NOTIFY_CHANNELS`. Each attempt is logged on **Administration → Message Log** (`view_notification_log`) with an honest status:

| Status | Meaning |
|---|---|
| Not sent (`skipped`) | Not attempted: channel has no provider, the member has no address or number, or email uses a development backend (console, file-based or dummy) |
| Queued | The provider accepted it but hasn't confirmed delivery yet (Twilio) |
| Sent | Handed over to the SMTP server, or to Notify.lk, which gives no delivery receipts |
| Delivered | The provider confirmed delivery (Twilio status callback) |
| Failed | The provider rejected it, or the request errored |

The app never shows "delivered" unless a provider has confirmed it.

```
NOTIFY_CHANNELS=email,sms,whatsapp    # any subset; empty = in-app only

# Email: a real SMTP backend
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=… EMAIL_PORT=587 EMAIL_HOST_USER=… EMAIL_HOST_PASSWORD=… EMAIL_USE_TLS=True DEFAULT_FROM_EMAIL=…

# SMS: Twilio or Notify.lk (Sri Lanka)
SMS_PROVIDER=twilio                   # or notifylk
TWILIO_ACCOUNT_SID=AC…
TWILIO_AUTH_TOKEN=…
TWILIO_SMS_FROM=+1…
NOTIFYLK_USER_ID=…  NOTIFYLK_API_KEY=…  NOTIFYLK_SENDER_ID=…

# WhatsApp: Twilio (needs an approved WhatsApp sender)
WHATSAPP_PROVIDER=twilio
TWILIO_WHATSAPP_FROM=+1…

# Public HTTPS address, for Twilio delivery callbacks
PUBLIC_BASE_URL=https://gym.example.com
```

With `PUBLIC_BASE_URL` set, Twilio posts status updates to `/notifications/webhooks/twilio/`. The webhook checks Twilio's `X-Twilio-Signature` with your auth token and rejects anything unsigned or forged (HTTP 403). A status never moves backwards, so a late "sent" can't overwrite "delivered". Without `PUBLIC_BASE_URL`, Twilio messages stay *Queued* in the log.

### Scheduled tasks

One command runs every periodic job: membership expiry and expired notices, payment-due reminders, appointment reminders, maintenance reminders, the low-stock sweep, publishing scheduled announcements, and closing stale visits. Every job is idempotent, and if one fails the others still run.

```bash
python manage.py run_scheduled_tasks          # run every 15 minutes
```

- **Linux (cron):** `*/15 * * * * cd /srv/buildme && .venv/bin/python manage.py run_scheduled_tasks >> logs/scheduler.log 2>&1`
- **Windows (Task Scheduler):** a task that runs `E:\fitnessapp\.venv\Scripts\python.exe manage.py run_scheduled_tasks`, starting in `E:\fitnessapp`, repeating every 15 minutes.

**Optional Celery + Redis.** `config/celery.py` and `apps/core/tasks.py` are ready, but Celery is **not installed** and isn't needed. To use it instead of cron:

```bash
pip install "celery[redis]"
# .env: CELERY_BROKER_URL=redis://localhost:6379/0
celery -A config worker -l info
celery -A config beat -l info      # runs run_scheduled_tasks every 15 minutes
```

## Announcements (Phase 5)

- Kinds: general, closure or holiday notice, event, promotion. Audiences: everyone, all members, members with an active membership, members of one plan, all staff, or one staff role.
- Each announcement has a publish time (it can be scheduled) and an optional expiry. It is visible only between the two, and only while active. Deactivating hides it without deleting it.
- On publishing, the audience can be notified in-app (on by default), and optionally through the external channels. Recipients are notified once. Editing or re-saving doesn't notify them again.
- Users see live notices on **Notices** and as a banner on the dashboard. Members can't open notices meant for staff (HTTP 403).
- Managing announcements requires `manage_announcements` (Gym Manager, Super Admin).

## Reports (Phase 5)

**Reports** (`view_reports`: Super Admin, Gym Manager, Accountant) lists the reports the user may open. Every figure comes from database records.

| Section | Reports |
|---|---|
| Members | Member registrations · Active & expired memberships · Membership renewals · Plan popularity |
| Attendance | Daily & monthly attendance · Member attendance history |
| Finance | Daily & monthly payments · Outstanding balances · Revenue collected · Expense report · Revenue vs expenses |
| Training | Training appointments (a trainer with report access sees only their own) |
| Operations | Equipment maintenance · Inventory & low stock |

Every report offers a date range (where relevant), search, pagination (50 rows), **CSV** (UTF-8 with BOM for Excel; cells starting with `= + - @` are prefixed with `'` to block spreadsheet formula injection), **PDF**, and a **print** view. Some reports need extra permissions as well as `view_reports`. For example, *Revenue vs expenses* needs both `view_payments` and `view_expenses`.

### Financial rules (`apps/reports/finance.py`)

These definitions are used everywhere: reports, dashboard and receipts.

| Figure | Definition |
|---|---|
| Collected (gross) | Completed, non-voided payments, by payment date. Pending and voided entries are excluded. |
| Refunds | Completed refunds, by refund date |
| **Net collected** | Gross collected − refunds |
| Invoiced | Membership charges (a cancelled membership counts only what the cancellation policy still charges) plus fees of non-cancelled sessions, by start date. This is billing, not cash, and is **never** added to collected. |
| Outstanding | Current unpaid balance (price − net paid) on non-cancelled memberships and sessions. Not revenue. |
| Operating expenses | Active (non-voided) expenses by expense date, excluding the *Equipment* category |
| Capital expenses | Active *Equipment* expenses (purchases) |
| **Estimated operating profit** | Net collected − operating expenses |
| After capital spend | Operating profit − capital expenses |

Each cost is counted once. A repair or stock purchase exists only as its linked expense, and the reports read expenses, not the equipment or stock tables. Trainer pay records aren't counted (enter payroll as a *Salaries* expense). Figures are cash-basis estimates, not audited accounts.

## Gym settings

**Administration → Gym Settings** (`/settings/`) needs the `manage_settings` permission. The Super Admin has it by default, and it can be granted to other roles in the permission matrix.

| Setting | Used for | Blank means |
|---|---|---|
| Gym name | Header, page titles, receipts, report PDFs, emails and SMS | `GYM_NAME` |
| Address, phone, email, website, business registration no. | Printed under the gym name on receipts (HTML and PDF) | Not shown |
| Receipt footer | Last line of every receipt, e.g. a thank-you or refund policy | Not shown |
| Membership expiry reminder days | When members are reminded before expiry, e.g. `14,7,1` | `MEMBERSHIP_EXPIRY_REMINDER_DAYS` |
| "Expiring soon" window | Expiring-soon list and dashboard panel | `MEMBERSHIP_EXPIRING_SOON_DAYS` |
| Payment due reminder days | How early an unpaid membership triggers a reminder | `PAYMENT_DUE_REMINDER_DAYS` |
| Equipment maintenance reminder days | How early managers hear about due maintenance | `EQUIPMENT_MAINTENANCE_REMINDER_DAYS` |
| Appointment reminder hours | How long before a session the reminder goes out | `APPOINTMENT_REMINDER_HOURS` |

Values are validated: reminder days are whole numbers in a sensible range. Every save is recorded in the activity log with the names of the fields that changed. Reminders that were already sent are never sent again after a change.

## Dashboard statistics

`apps/dashboard/registry.py` defines a slot for each statistic: total members, active members, expired memberships, new this month, today's attendance, today's revenue, monthly revenue, and outstanding payments. It also defines the panels: member growth chart, revenue chart, upcoming expirations, and recent activities. Each slot is shown only to roles holding its permission.

Feature apps register a database-backed provider for a slot:

```python
# apps/<module>/metrics.py, imported from AppConfig.ready()
from apps.dashboard.registry import register

@register("today_revenue")
def today_revenue(user):
    return Payment.objects.filter(paid_on=timezone.localdate()).aggregate(s=Sum("amount"))["s"] or 0
```

These slots now have live data:

| Statistic | Definition |
|---|---|
| Total Members | All member profiles, with the number of inactive members shown underneath |
| Active Members | Active members with a non-suspended membership covering today |
| Expired Memberships | Active members whose last period has ended and wasn't renewed |
| New This Month | Members registered from the 1st of the current month up to today |
| Upcoming Expirations | Unrenewed current periods ending within `MEMBERSHIP_EXPIRING_SOON_DAYS` |
| New Members chart | Registrations per month over the last 6 months |
| Recent Activities | The latest activity-log entries |

Phase 3 adds:

| Statistic | Definition |
|---|---|
| Today's Attendance | Check-ins today, unique members, and how many are in the gym now |
| Collected Today / This Month | Completed payments minus refunds, by payment date. Pending and voided entries are excluded. |
| Outstanding (Unpaid) | Sum of outstanding balances on non-cancelled memberships. This is **never** counted as revenue. |
| Revenue collected chart | Net collected per month, last 6 months |
| Recent Payments | The latest non-voided transactions |
| Membership Payment Status | Current and scheduled memberships grouped by payment status |
| Today's Check-ins chart | Check-ins by hour |
| Trainers | All trainer profiles (and how many are active) |
| Upcoming Sessions | Booked sessions from now on. **A trainer sees only their own.** |
| Sessions Completed | Completed this month (trainer: their own) |
| Workout Plans Active | Current member plans (trainer: their members' plans) |
| Expenses This Month | Active operating expenses this month. Equipment (capital) spend is shown in the note (`view_expenses`). |
| Est. Operating Profit | This month's net collected minus operating expenses (`view_payments` + `view_expenses`) |
| Equipment Needing Attention | Out of service, under maintenance, open faults, or maintenance overdue |
| Low Stock Items | Items at or below their minimum level |
| Revenue vs Expenses chart | Net collected against operating expenses per month, last 6 months |

The dashboard also shows a banner for up to three live announcements visible to the user.
 No hard-coded numbers are shown. If a provider fails, it is logged and only that card shows an error state.

## Security

- CSRF protection on every form. Sign-out is POST-only.
- Login throttling: 5 failed attempts per username and IP address lock that pair out for 15 minutes (configurable).
- Sign-in works with username or email. Error messages are generic and don't reveal whether an account exists.
- Password strength validators apply to new, changed and reset passwords. Reset links expire after 24 hours.
- Deactivated users lose access on their next request.
- Secrets come from environment variables. Without `DJANGO_SECRET_KEY` and with `DJANGO_DEBUG=False`, the app refuses to start.
- With `DJANGO_DEBUG=False`, these are on: secure cookies, HSTS, SSL redirect, `X-Frame-Options: DENY`, nosniff.
- Custom 400, 403, 404 and 500 pages show no technical details. Errors are logged on the server.
- The activity log records sign-ins, failed sign-ins (username only, never the password), account and permission changes, every financial action (payments, refunds, voids, adjustments, expenses), stock movements, equipment changes, announcements and gym-settings changes.
- Uploaded files (member and trainer photos, certificates, expense receipts, exercise images) are **never served from `/media/`**. Each one is delivered by a view that checks the viewer's permission. Uploads are checked by content, not just by name (a real JPEG/PNG/WebP or PDF; SVG and disguised files are rejected), size-limited and stored under random names.
- Members only ever see their own records. Trainers see health and progress data only for members they currently coach, and lose access when the assignment ends. Pages never link to a page the viewer would be refused.
- A member's QR check-in pass works as an entry credential, so only front-desk roles (`manage_attendance`) and the member themselves can open it.
- Payments are recorded inside database transactions with row locks and a one-time submission token, so a double-click or a race can't record a payment twice. Card numbers are rejected in every free-text field.
- Passwords are stored only as salted hashes (Django's PBKDF2). They are never logged or shown.

## Deployment

The app is a standard Django WSGI project. The steps below are for a Linux server (Ubuntu or similar) with PostgreSQL, gunicorn and nginx. The same settings work on a PaaS such as Render, Railway or Fly.io.

### 1. Server and code

```bash
sudo apt install python3 python3-venv postgresql nginx
sudo -u postgres createuser --pwprompt buildme
sudo -u postgres createdb --owner=buildme buildme
git clone <your repository> /srv/buildme && cd /srv/buildme      # or copy the project folder
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment (`/srv/buildme/.env`)

Copy `.env.example` to `.env` and set at least:

```
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<run: python -c "from django.core.management.utils import get_random_secret_key as k; print(k())">
DJANGO_ALLOWED_HOSTS=gym.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://gym.example.com
DATABASE_URL=postgres://buildme:PASSWORD@localhost:5432/buildme
USE_X_FORWARDED_PROTO=True                 # nginx terminates HTTPS
DJANGO_ADMIN_URL=<something-unguessable>/  # optional: moves /admin/
MEDIA_ROOT=/srv/buildme-media              # private upload folder, outside the web root
CACHE_BACKEND=django.core.cache.backends.db.DatabaseCache   # login throttling shared by all workers
CACHE_LOCATION=cache_table
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend   # plus EMAIL_HOST, EMAIL_HOST_USER, ...
DEFAULT_FROM_EMAIL=gym@example.com
```

With `DJANGO_DEBUG=False`, the app refuses to start without a secret key. HTTPS redirect, HSTS (one year), secure cookies, `X-Frame-Options: DENY`, nosniff and a same-origin referrer policy are switched on automatically. Each can be adjusted in `.env` (see the Security block in `.env.example`). `SECURE_HSTS_PRELOAD` stays off unless you deliberately opt in.

### 3. Database, static files, first admin

```bash
python manage.py migrate                 # creates tables and the six role groups
python manage.py createcachetable        # for the database cache above
python manage.py collectstatic --noinput # into staticfiles/ (or STATIC_ROOT)
python manage.py createsuperuser         # the first Super Admin
python manage.py check --deploy          # should report no errors
```

**Static files** are served by WhiteNoise from `staticfiles/`, with content-hashed names and long cache lifetimes, so no extra web-server configuration is needed. **Uploaded files** stay in `MEDIA_ROOT` and are delivered only by the app after a permission check. Do **not** expose that folder through nginx.

### 4. gunicorn (systemd)

`/etc/systemd/system/buildme.service`:

```ini
[Unit]
Description=BUILDME gym management
After=network.target postgresql.service

[Service]
User=www-data
WorkingDirectory=/srv/buildme
ExecStart=/srv/buildme/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now buildme
```

### 5. nginx and HTTPS

```nginx
server {
    server_name gym.example.com;
    client_max_body_size 10M;               # largest upload (receipts/certificates) plus margin
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Get a certificate with `sudo certbot --nginx -d gym.example.com`.

### 6. Scheduled tasks

Run `python manage.py run_scheduled_tasks` every 15 minutes (cron or Task Scheduler; see *Scheduled tasks*). It covers reminders, expiry notices, low stock, maintenance, scheduled announcements and closing stale visits.

`TIME_ZONE` defaults to `Asia/Colombo`; change it if the gym is elsewhere. Access hours, "today", reminders and daily reports use it. Timestamps are stored in UTC, so changing it never alters saved data.

### 7. Updating to a new version

```bash
cd /srv/buildme && . .venv/bin/activate
python manage.py backup_database --media          # always back up first
git pull                                         # or copy the new files
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart buildme
```

### Windows hosting

gunicorn doesn't run on Windows. For a small single-PC installation, use `pip install waitress` and then `waitress-serve --listen=127.0.0.1:8000 config.wsgi:application` behind IIS or a reverse proxy, with the same `.env`. Schedule `run_scheduled_tasks` and `backup_database` in Task Scheduler.

## Backups

```bash
python manage.py backup_database                  # database only, into ./backups/
python manage.py backup_database --media          # also zips uploaded files
python manage.py backup_database --output /mnt/backup/buildme
```

- **SQLite:** uses SQLite's online backup API, so it's safe while the app is running. The result is a complete `.sqlite3` file. To restore, stop the app and put the file back as `db.sqlite3`.
- **PostgreSQL:** runs `pg_dump --format=custom` (the PostgreSQL client tools must be installed). To restore: `pg_restore --clean --no-owner --dbname=buildme db-YYYYMMDD-HHMMSS.dump`.
- **Uploaded files** live in `MEDIA_ROOT` and are not inside the database. Back them up too (`--media`).
- Schedule a daily backup, for example `0 2 * * * cd /srv/buildme && .venv/bin/python manage.py backup_database --media`, and copy `backups/` to another machine or cloud storage. Test a restore occasionally.
- Back up before every upgrade or migration.

## Running tests

```bash
python manage.py test
```

The tests cover:

- **Registration:** valid sign-up, duplicate username and email, invalid email, mismatched and weak passwords, role injection, sign-in and sign-out with the new account, the disable toggle, CSRF
- **Authentication:** login with username or email, lockout, logout, remember-me, open-redirect protection, CSRF, password change, the full password-reset flow
- **Permissions:** default role permissions, group syncing, the role hierarchy, member isolation, escalation attempts, the permission matrix
- **Dashboard:** access per role, widget visibility, database-driven counts (including member and membership statistics), empty-database behaviour, pending and error states
- **Members:** registration, unique IDs, phone normalisation, validation, duplicate prevention (form and database), photo validation and protected serving, search, filters, sorting, pagination, editing, deactivation and reactivation, history preservation, portal-login linking, permissions per role
- **Plans:** standard and custom durations, invalid prices and durations, duplicate names, price-change permission (including a crafted POST), deactivation, preserved historical terms
- **Attendance:** every check-in rule (inactive member, no, expired, scheduled, suspended or cancelled membership), access days and hours, taking rules from the membership snapshot, duplicate prevention (service and database constraint), re-entry after check-out, stale-visit closing, balance warnings, QR check-in, forged and invalid codes, no personal data in the QR, pass reissue, permissions, history filters, daily report, dashboard counts
- **Payments:** full and partial payments, overpayment refusal, pending and confirm, duplicate submission (including a simulated race), rollback, card-number rejection, refunds and their limits, voids, adjustments, cancelled-membership policy, database and Python balance agreement, receipts (content, as-at figures, PDF, non-Latin names, access control, pending and void), role permissions on every action, list filters and totals, outstanding page, financial dashboard figures
- **Trainers:** automatic profiles, trainer creation and setup email, self-edit limits, availability overlap, certificate content checks, assignments (duplicates, history, immediate end), pay-record privacy
- **Appointments:** fee and duration snapshot, trainer and member overlap, availability, time off, horizon, past times, membership requirement, inactive trainer or session type, free-slot calculation, database duplicate guard, simulated race, reschedule, cancel, outcome timing, notifications, reminder idempotency, session fees through payments (partial, overpayment, cancel, refund, receipts, outstanding), member booking limited to assigned trainers, the 24-hour cutoff and staff override, ownership checks, trainer-only schedule, session-type permissions, notification privacy
- **Workouts / progress / nutrition:** exercise library permissions, plan building and validation, copy-on-assign isolation, coaching scope, member view and logging (log snapshot preserved), archiving, receptionist exclusion, progress recording, charts, validation, privacy (other members, unassigned trainers, front desk; access removed when an assignment ends), meal plans, nutrition profile privacy, module switch-off, role-scoped training dashboard, member portal links
- **Phase 5 (`apps/reports/tests/test_phase5.py`):** inventory ledger (purchase, usage, balances after each movement, no negative stock through the service or the database, adjustment reasons, low-stock alert once and re-arm, staff limited to usage and sale); expenses (Accountant records, Manager view-only, other roles 403, validation, receipt checks and protected download, voids excluded from totals, linked expenses not editable); equipment (auto code, a single linked purchase expense, locked cost, unsafe fault taking equipment out of service and notifying managers, maintenance resolving the fault with a linked expense and next date, deduplicated reminders, permissions); notifications (duplicate keys, running the scheduler twice, expiry, expired and payment-due, payment-received only once confirmed, renewal, no external sends without configuration, honest statuses for missing providers, development email backends, Twilio queued, failed and signed or forged webhooks, Notify.lk sent-not-delivered, centre filters, message-log permission); announcements (notify once, audience targeting, staff-only visibility, scheduled and expired, plan audience); reports (financial rules with refunds, pending, voids and capital spend, every report as HTML, CSV, PDF and print, CSV injection, per-report permissions, dashboard figures)
- **Membership lifecycle:** month-end and leap-year arithmetic, assignment, overlap prevention, early and late renewal, upgrade and downgrade, suspension and reactivation, cancellation, invalid transitions, expiring and lapsed queries, transaction rollback, permission checks on every POST action
- **Shared UI:** navigation per role, error pages
- **End-to-end workflows (`apps/core/test_workflows.py`):** registration → membership → payment → receipt → status; attendance search, validation, refusal and history; renewal with history kept and dashboard figures; partial payments; trainer creation, availability, booking, overlap and outside-hours refusal, completion; exercise → plan → assignment, with the member able to read but not change it; expense and payment flowing into reports and dashboard; expiry reminder created once and shown in the right notification centre; gym-settings permissions, validation and effect on receipts
- **Security regressions (`apps/core/test_security.py`):** QR-pass access, uploads never served from `/media/`, no links to forbidden pages, password hashing, disguised and SVG uploads rejected, trainer access removed when an assignment ends

## Roadmap

- **Phase 2:** Members and memberships (done)
- **Phase 3:** Attendance, payments, billing and receipts (done)
- **Phase 4:** Trainers, appointments, workouts, progress, nutrition (done)
- **Phase 5:** Equipment, inventory, expenses, notifications, announcements, reports (done)
- **Final integration:** gym settings, security review, UI polish, deployment preparation (done)
- **Later:** Group classes

The sidebar entries for these modules already exist and are marked "Soon". Each one is shown only to roles that hold the module's permission.
