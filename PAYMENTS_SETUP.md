# RentX customer accounts and Razorpay setup

## Customer accounts
- Open **Create account** in the navigation, register with your name, email, phone, and an 8+ character password.
- Passwords are stored as password hashes, not plain text.
- Sign in to book a car and see only your own reservations under **My Bookings**.
- Booking records are migrated in-place in `rentx_web.db`; existing cars and bookings are not deleted. Older bookings cannot be assigned to a user automatically, so they remain admin-visible only.

## Configure the real payment gateway (Razorpay)
A real gateway requires your own Razorpay account and API keys. This project does not contain keys and will not take payments until configured.

1. Create/sign in to your Razorpay account and enable **Test Mode**.
2. In the Razorpay Dashboard, generate a Test Key ID and Test Key Secret.
3. Copy `.env.example` to a new file named `.env` in this folder.
4. Replace `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` with your own TEST credentials. Do not share or commit `.env`.
5. Replace `RENTX_SECRET_KEY` and `RENTX_ADMIN_PASSWORD` with strong private values.
6. Install dependencies and restart the server:

   ```powershell
   python -m pip install -r requirements.txt
   python app.py
   ```

7. Register a customer, sign in, create a booking, and use the Razorpay checkout button. Test with the test payment methods shown in your Razorpay account/dashboard.
8. Only after successful test transactions and appropriate business verification, switch to LIVE mode and use live credentials. Never use real card data in test mode.

## Security and production notes
- The server creates Razorpay orders and verifies the Razorpay signature before marking a booking as paid.
- Amounts are calculated server-side from the car price and rental duration.
- Never expose `RAZORPAY_KEY_SECRET` in HTML/JavaScript or commit it to source control.
- This is a local educational project. Before public production deployment, add CSRF protection, rate limiting, HTTPS, stronger secret management, payment webhook handling/reconciliation, robust date-overlap availability checks, logging, backups, and a production WSGI server.
- The configured checkout is a real Razorpay integration, but transactions cannot be completed until the owner supplies valid gateway credentials and has an active account.

## Real-time confirmation (added)
- **Webhook**: in Razorpay Dashboard > Settings > Webhooks add `https://YOUR-DOMAIN/webhooks/razorpay`, enable `payment.captured`, `order.paid`, `payment.failed`, and put the secret in `RAZORPAY_WEBHOOK_SECRET`. For local testing expose port 5000 with a tunnel (e.g. ngrok).
- Bookings confirm via 3 independent paths: browser signature check + Razorpay payment lookup, webhook, and a status poll that reconciles with Razorpay. All are idempotent.
- No keys set? A clearly labelled **sandbox** button records a "Paid (Demo)" booking. It is disabled as soon as keys exist.

## v3 changes: QR payments, bills, hourly rentals, car photos
**Hourly / daily rentals** – customers pick a pick-up date + time and rent for *N hours (1–23)* or *N days (1–60)*; the return time is shown live. Hourly price = `HOURLY_FACTOR` (default 12%) of the daily rate per hour, never more than one day. Overlap checks use exact times.

**Real-time QR payment** (the payment page picks the best mode automatically):
1. **Razorpay keys set** → a fixed-amount, single-use Razorpay UPI QR is created per booking (needs *QR Codes* enabled on your Razorpay account; otherwise the normal Razorpay checkout button still works). Payment is detected by webhook (`qr_code.credited`, `payment.captured`) and by polling every 3 s, then the page jumps to the bill.
2. **Only `UPI_VPA` set** → a real UPI QR with the exact amount and booking reference is shown. A plain UPI QR can't notify a website, so the customer enters the 12-digit UTR and you press **Verify paid** in Admin → Recent bookings.
3. **Nothing set** → demo QR; scanning it on a phone (run with `RENTX_HOST=0.0.0.0`) confirms a *demo* payment and the desktop page updates live. No money moves.

**Bill** – after payment: `/invoice/<id>.pdf` (downloadable PDF with verification QR) plus an on-screen bill.

**Car photos** – photos are fetched from the exact Wikipedia article of each model (with search fallback and image validation). Admin → Fleet → *Re-download real car photos* retries. Your own photo always wins: save it as `static/cars/<car-name-slug>.jpg`, e.g. `static/cars/toyota-fortuner.jpg`, `hyundai-creta.jpg`, `maruti-suzuki-swift.jpg`, then restart.
