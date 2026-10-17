"""Run:  python selftest.py   -> checks booking, payment, bill page and PDF generation on YOUR machine."""
import os, sys, tempfile
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as A

A.DB = os.path.join(tempfile.mkdtemp(), 'selftest.db')
A.razorpay_client = lambda: None; A.UPI_VPA = ''          # force sandbox mode for the test
A.init_db(); c = A.app.test_client()
def tok():
    c.get('/about')
    with c.session_transaction() as s: return s.get('csrf')
def check(label, ok):
    print(('PASS ' if ok else 'FAIL ') + label); 
    if not ok: sys.exit(1)

check('register', c.post('/register', data={'csrf_token': tok(), 'full_name': 'Self Test', 'email': 's@t.com', 'phone': '9876543210', 'password': 'password1', 'confirm_password': 'password1'}).status_code == 302)
d = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
r = c.post('/booking', data={'csrf_token': tok(), 'car_id': 1, 'name': 'Self Test', 'phone': '9876543210', 'email': 's@t.com', 'start_date': d, 'start_time': '10:00', 'duration_unit': 'days', 'duration_value': 2})
check('create booking', r.status_code == 302 and '/payment/1' in r.headers['Location'])
check('payment page', c.get('/payment/1').status_code == 200)
check('demo payment', c.post('/payment/1/demo', data={'csrf_token': tok()}).status_code == 302)
check('bill page', c.get('/receipt/1').status_code == 200)
r = c.get('/invoice/1.pdf')
check('PDF invoice (%d bytes)' % len(r.data), r.status_code == 200 and r.data.startswith(b'%PDF'))
print('\nAll good. Bills and PDFs work on this machine.')
