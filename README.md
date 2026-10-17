# RentX — Flask Car Rental Website

RentX is a Flask website with HTML/CSS/JavaScript templates and an SQLite database. It includes customer accounts, personal booking history, a Razorpay Checkout integration, fleet browsing, contact messages, and an admin dashboard.

## Run on Windows

1. Open this folder in VS Code.
2. In Terminal, run:

   ```powershell
   python -m pip install -r requirements.txt
   python app.py
   ```

3. Visit http://127.0.0.1:5000 and keep the terminal open.

## Customer flow

- Register at `/register` or sign in at `/login`.
- Signed-in customers can book vehicles and view their own bookings at `/my-bookings`.
- Unpaid bookings have a Pay Now action that opens Razorpay Checkout after the gateway is configured.

## Admin

- Admin login: `/admin/login`
- Default demo credentials if environment variables are not configured: `admin` / `RentX@123`.
- Change the default admin password and `RENTX_SECRET_KEY` before using this outside a local demo.

## Payment gateway

Read `PAYMENTS_SETUP.md`. Razorpay Test Mode keys must be added to a local `.env` file before checkout can collect payments. The `.env` file is private and is not included in this ZIP. The app will clearly indicate that payment setup is missing if keys are absent.

## Notes

- SQLite database file: `rentx_web.db`.
- Existing database tables are migrated on startup without deleting old records.
- This is an educational project and needs additional security, webhook reconciliation, HTTPS, and production hosting configuration before public deployment.

## Real car photos
On startup RentX downloads each car's real photo from its Wikipedia article into `static/cars/` (needs internet once). Cars added by an admin without an image URL are fetched by name automatically. Until a photo arrives a local placeholder is shown.

## Troubleshooting bills / PDF
- Run `python selftest.py` – it tests booking → payment → bill → PDF on your machine.
- If you copied `.env.example` to `.env`, only *uncomment and fill in* the Razorpay / `UPI_VPA` lines you really use. Placeholder values are now ignored (sandbox mode stays on), but invalid real-looking keys will make order creation fail.
- If the PDF can't be generated the customer is sent to the on-screen bill (use Print) and the error is written to the terminal.
- Admin → Recent bookings now shows 50 bookings, a **Verify paid** button for UPI/UTR payments, and a **Bill PDF** button for paid bookings.
