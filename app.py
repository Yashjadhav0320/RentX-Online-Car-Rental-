from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, Response
import sqlite3, os, re, json, secrets, threading, urllib.request, urllib.parse, math, io
from datetime import date, datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

try:
    import razorpay
except ImportError:
    razorpay = None

def _env(name, default=''):
    """Read an env var; treat blank values and the .env.example placeholders as 'not set'."""
    v = os.environ.get(name, default).strip()
    low = v.lower()
    if not v or 'your_' in low or low.startswith('replace-with') or low in ('yourname@okhdfcbank', 'change-this-admin-password'):
        return default
    return v


BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'rentx_web.db')
app = Flask(__name__)
app.secret_key = _env('RENTX_SECRET_KEY', 'change-this-demo-secret-key-before-deployment')
ADMIN_USER = os.environ.get('RENTX_ADMIN_USER', 'admin')
ADMIN_PASS = _env('RENTX_ADMIN_PASSWORD', 'RentX@123')
RAZORPAY_KEY_ID = _env('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = _env('RAZORPAY_KEY_SECRET')
RAZORPAY_WEBHOOK_SECRET = _env('RAZORPAY_WEBHOOK_SECRET')
UPI_VPA = _env('UPI_VPA')          # your own UPI ID, e.g. yourname@okhdfcbank
UPI_NAME = os.environ.get('UPI_PAYEE_NAME', 'RentX Car Rental')
HOURLY_FACTOR = float(os.environ.get('HOURLY_FACTOR', '0.12'))  # 1 hour = 12% of the daily rate (capped at 1 day)
HOLD_MINUTES = 30  # unpaid bookings hold a car this long
IMG_DIR = os.path.join(BASE, 'static', 'cars')
os.makedirs(IMG_DIR, exist_ok=True)


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    return con


def init_db():
    with db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS cars(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,registration TEXT UNIQUE NOT NULL,category TEXT NOT NULL,price REAL NOT NULL,image TEXT,description TEXT,available INTEGER NOT NULL DEFAULT 1)''')
        c.execute('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,full_name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,phone TEXT NOT NULL,password_hash TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY AUTOINCREMENT,car_id INTEGER NOT NULL,name TEXT NOT NULL,phone TEXT NOT NULL,email TEXT,start_date TEXT NOT NULL,end_date TEXT NOT NULL,days INTEGER NOT NULL,total REAL NOT NULL,status TEXT NOT NULL DEFAULT 'Pending Payment',created_at TEXT DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(car_id) REFERENCES cars(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,email TEXT,subject TEXT,message TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        # Migrate databases created by older RentX versions without deleting existing records.
        bcols = {r['name'] for r in c.execute('PRAGMA table_info(bookings)').fetchall()}
        for col, definition in [('user_id', 'INTEGER REFERENCES users(id)'), ('payment_status', "TEXT NOT NULL DEFAULT 'Unpaid'"), ('payment_id', 'TEXT'), ('payment_order_id', 'TEXT'), ('start_dt', 'TEXT'), ('end_dt', 'TEXT'), ('duration_unit', "TEXT DEFAULT 'days'"), ('duration_value', 'INTEGER'), ('pay_token', 'TEXT'), ('qr_id', 'TEXT'), ('utr', 'TEXT')]:
            if col not in bcols:
                c.execute(f'ALTER TABLE bookings ADD COLUMN {col} {definition}')
        c.execute('''CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT,booking_id INTEGER NOT NULL,order_id TEXT,payment_id TEXT UNIQUE,amount REAL,status TEXT,method TEXT,source TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(booking_id) REFERENCES bookings(id))''')
        ccols = {r['name'] for r in c.execute('PRAGMA table_info(cars)').fetchall()}
        for col, definition in [('seats', 'INTEGER DEFAULT 5'), ('fuel', "TEXT DEFAULT 'Petrol'"), ('transmission', "TEXT DEFAULT 'Manual'"), ('wiki', 'TEXT')]:
            if col not in ccols:
                c.execute(f'ALTER TABLE cars ADD COLUMN {col} {definition}')
        count = c.execute('SELECT COUNT(*) FROM cars').fetchone()[0]
        if count == 0:
            for row in FLEET:
                c.execute('INSERT INTO cars(name,registration,category,price,image,description,seats,fuel,transmission,wiki) VALUES(?,?,?,?,?,?,?,?,?,?)', row)
        # Existing databases: fill specs / wiki titles for known cars without touching bookings.
        for r in c.execute("SELECT id,name,category,image FROM cars").fetchall():
            if not r['image'] or not os.path.exists(os.path.join(BASE, r['image'].lstrip('/'))):
                c.execute('UPDATE cars SET image=? WHERE id=?', (local_car_image(r['name']) or make_car_svg(r['name'], r['category']), r['id']))
        for row in FLEET:
            c.execute("UPDATE cars SET seats=?,fuel=?,transmission=?,wiki=COALESCE(wiki,?) WHERE name=? AND wiki IS NULL", (row[6], row[7], row[8], row[9], row[0]))


# name, registration, category, price/day (INR), image, description, seats, fuel, transmission, Wikipedia article
FLEET = [
    ('BMW 3 Series','MH12AB1001','Luxury',6500,'','Sharp handling and an executive cabin for business or weekend drives.',5,'Petrol','Automatic','BMW 3 Series (G20)'),
    ('Mercedes-Benz C-Class','MH12AB1002','Luxury',7200,'','Refined cabin, smooth ride and unmistakable three-pointed-star presence.',5,'Petrol','Automatic','Mercedes-Benz C-Class (W206)'),
    ('Audi A4','MH12AB1003','Premium',6000,'','Quattro-grade confidence with a modern, tech-rich interior.',5,'Petrol','Automatic','Audi A4'),
    ('Toyota Fortuner','MH12AB1006','SUV',5500,'','Commanding 7-seat SUV built for long highway adventures.',7,'Diesel','Automatic','Toyota Fortuner'),
    ('Mahindra Thar','MH12AB1007','SUV',4200,'','Iconic off-roader for hill trips and weekend escapes.',4,'Diesel','Manual','Mahindra Thar'),
    ('Toyota Innova Crysta','MH12AB1008','SUV',3800,'','The family favourite: roomy, reliable and made for groups.',7,'Diesel','Manual','Toyota Innova Crysta'),
    ('Hyundai Creta','MH12AB1004','SUV',2800,'','Comfortable compact SUV with space for the whole crew.',5,'Petrol','Automatic','Hyundai Creta'),
    ('Tata Nexon','MH12AB1009','SUV',2400,'','5-star safety-rated compact SUV, great in the city and beyond.',5,'Petrol','Manual','Tata Nexon'),
    ('Honda City','MH12AB1010','Premium',2600,'','Smooth, spacious sedan that is a joy on highways.',5,'Petrol','Automatic','Honda City'),
    ('Maruti Suzuki Baleno','MH12AB1005','Standard',1800,'','Easy city driving with excellent fuel economy and everyday value.',5,'Petrol','Manual','Maruti Suzuki Baleno'),
    ('Maruti Suzuki Swift','MH12AB1011','Standard',1600,'','Nimble, fun hatchback that is easy to park anywhere.',5,'Petrol','Manual','Maruti Suzuki Swift'),
]


def calc_total(price, unit, value):
    price = float(price)
    if unit == 'hours':
        return round(min(price * HOURLY_FACTOR * value, price), 2)  # never dearer than a full day
    return round(price * value, 2)


def duration_label(unit, value):
    value = int(value or 0)
    u = 'hour' if unit == 'hours' else 'day'
    return f"{value} {u}{'' if value == 1 else 's'}"


def fmt_dt(v):
    try:
        return datetime.strptime(v, '%Y-%m-%d %H:%M').strftime('%d %b %Y, %I:%M %p')
    except Exception:
        return v or ''


def booking_times(b):
    """Works for new (datetime) and old (date-only) bookings."""
    s = b['start_dt'] or f"{b['start_date']} 00:00"
    e = b['end_dt'] or f"{b['end_date']} 00:00"
    unit = b['duration_unit'] or 'days'
    val = b['duration_value'] or b['days']
    return s, e, unit, val


app.jinja_env.filters['dt'] = fmt_dt
app.jinja_env.globals['duration_label'] = duration_label
app.jinja_env.globals['booking_times'] = booking_times


def qr_svg(data, scale=6, border=3):
    """Pure-ReportLab QR code rendered as inline SVG (no extra packages)."""
    from reportlab.graphics.barcode import qrencoder as qe
    q = None
    for ver in range(3, 41):
        try:
            q = qe.QRCode(ver, qe.QRErrorCorrectLevel.M); q.addData(data); q.make(); break
        except Exception:
            q = None
    if q is None:
        return None  # data too long / not encodable -> caller falls back to an <img> or message
    n = q.getModuleCount(); size = (n + border * 2) * scale
    rects = ''.join(f'<rect x="{(c+border)*scale}" y="{(r+border)*scale}" width="{scale}" height="{scale}"/>' for r in range(n) for c in range(n) if q.isDark(r, c))
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}" shape-rendering="crispEdges"><rect width="{size}" height="{size}" fill="#fff"/><g fill="#000">{rects}</g></svg>'


def upi_link(booking_id, amount):
    q = urllib.parse.urlencode({'pa': UPI_VPA, 'pn': UPI_NAME, 'am': f'{float(amount):.2f}', 'cu': 'INR', 'tn': f'RentX RX-{booking_id:05d}', 'tr': f'RX{booking_id:05d}'}, quote_via=urllib.parse.quote)
    return 'upi://pay?' + q

UA = {'User-Agent': 'RentX/1.0 (student car rental project; Wikipedia REST API client)'}


def slug(t):
    return re.sub(r'[^a-z0-9]+', '-', t.lower()).strip('-')


def _get_json(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15))


def _wiki_image_for(title):
    t = urllib.parse.quote(title.replace(' ', '_'), safe='()')
    d = _get_json(f'https://en.wikipedia.org/api/rest_v1/page/summary/{t}')
    if d.get('type') == 'disambiguation':
        return None
    return (d.get('originalimage') or d.get('thumbnail') or {}).get('source')


def _wiki_search_image(query):
    q = urllib.parse.urlencode({'action': 'query', 'generator': 'search', 'gsrsearch': query, 'gsrlimit': 3, 'prop': 'pageimages', 'piprop': 'original', 'format': 'json'})
    pages = (_get_json('https://en.wikipedia.org/w/api.php?' + q).get('query') or {}).get('pages', {})
    for p in sorted(pages.values(), key=lambda x: x.get('index', 99)):
        if p.get('original', {}).get('source'):
            return p['original']['source']
    return None


CAR_STYLE = {  # name -> (body shape, colour)
    'BMW 3 Series': ('sedan', '#2f66b3'), 'Mercedes-Benz C-Class': ('sedan', '#aeb6bf'), 'Audi A4': ('sedan', '#a61e2a'),
    'Toyota Fortuner': ('suv', '#e6e8ea'), 'Mahindra Thar': ('thar', '#c9582c'), 'Toyota Innova Crysta': ('suv', '#8a929c'),
    'Hyundai Creta': ('suv', '#f1f3f5'), 'Tata Nexon': ('suv', '#1f8a9e'), 'Honda City': ('sedan', '#8f2434'),
    'Maruti Suzuki Baleno': ('hatch', '#3d78c0'), 'Maruti Suzuki Swift': ('hatch', '#d03030'),
}
CAR_SHAPES = {
    'sedan': ('M120 300 L120 262 Q120 245 140 240 L250 222 L320 160 Q330 150 345 150 L505 150 Q520 150 530 160 L585 222 L660 240 Q680 245 680 262 L680 300 Z', 'M338 166 L500 166 L556 222 L292 222 Z', 418),
    'suv': ('M110 305 L110 250 Q110 232 130 226 L215 205 L255 145 Q262 135 275 135 L560 135 Q575 135 582 145 L610 205 L675 226 Q692 232 692 250 L692 305 Z', 'M278 152 L556 152 L585 205 L250 205 Z', 400),
    'hatch': ('M130 300 L130 258 Q130 244 148 240 L250 226 L310 158 Q320 148 335 148 L500 148 Q515 148 525 158 L600 232 Q660 240 672 256 L672 300 Z', 'M345 165 L495 165 L558 228 L302 228 Z', 420),
    'thar': ('M130 305 L130 195 L180 195 L190 150 L600 150 L610 195 L670 195 L670 305 Z', 'M208 166 L582 166 L588 195 L202 195 Z', 395),
}


def make_car_svg(name, category='', overwrite=False):
    """Named car picture (body shape + colour) that always works offline; replaced by the real photo when it downloads."""
    from xml.sax.saxutils import escape
    path = os.path.join(IMG_DIR, slug(name) + '.svg')
    if overwrite or not os.path.exists(path):
        shape, colour = CAR_STYLE.get(name, ('sedan', '#e9b85d'))
        body, win, pillar = CAR_SHAPES[shape]
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450"><defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2a3038"/><stop offset="1" stop-color="#12151a"/></linearGradient></defs>'
               f'<rect width="800" height="450" fill="url(#bg)"/><rect y="330" width="800" height="120" fill="#0d0f12"/><ellipse cx="400" cy="338" rx="300" ry="14" fill="#000" opacity=".5"/>'
               f'<path d="{body}" fill="{colour}"/><path d="{body}" fill="none" stroke="#000" stroke-opacity=".25" stroke-width="3"/>'
               f'<path d="{win}" fill="#bcd6e8" opacity=".9"/><line x1="{pillar}" y1="150" x2="{pillar}" y2="226" stroke="{colour}" stroke-width="8"/>'
               f'<rect x="640" y="252" width="40" height="14" rx="6" fill="#fff6c8"/><rect x="120" y="254" width="26" height="12" rx="5" fill="#d33"/>'
               f'<g><circle cx="250" cy="312" r="46" fill="#0b0d10"/><circle cx="250" cy="312" r="24" fill="#c7ccd2"/><circle cx="250" cy="312" r="8" fill="#555"/>'
               f'<circle cx="550" cy="312" r="46" fill="#0b0d10"/><circle cx="550" cy="312" r="24" fill="#c7ccd2"/><circle cx="550" cy="312" r="8" fill="#555"/></g>'
               f'<text x="400" y="393" text-anchor="middle" font-family="Arial,sans-serif" font-size="36" font-weight="700" fill="#fff">{escape(name)}</text>'
               f'<text x="400" y="424" text-anchor="middle" font-family="Arial,sans-serif" font-size="18" fill="#e9b85d" letter-spacing="4">{escape(category.upper())}</text></svg>')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(svg)
    return f'/static/cars/{slug(name)}.svg'


def local_car_image(name):
    """A photo you drop into static/cars/<slug>.jpg|png|webp always wins."""
    for ext in ('.jpg', '.jpeg', '.png', '.webp'):
        if os.path.exists(os.path.join(IMG_DIR, slug(name) + ext)):
            return f'/static/cars/{slug(name)}{ext}'
    return None


def fetch_car_image(car_id, name, wiki=None):
    """Use a local photo if present, else download the real photo of this exact car model from Wikipedia."""
    loc = local_car_image(name)
    if loc:
        with db() as c:
            c.execute('UPDATE cars SET image=? WHERE id=?', (loc, car_id))
        return loc
    src = None
    for how in (lambda: _wiki_image_for(wiki) if wiki else None, lambda: _wiki_image_for(name), lambda: _wiki_search_image(name + ' car')):
        try:
            src = how()
        except Exception as e:
            app.logger.warning('Image lookup failed for %s: %s', name, e)
        if src and re.search(r'\.(jpe?g|png)$', src, re.I):
            break
        src = None
    if not src:
        return None
    try:
        orig = src
        m = re.match(r'(https://upload\.wikimedia\.org/wikipedia/commons)/([0-9a-f]/[0-9a-f]{2})/([^/]+\.(?:jpe?g|png))$', src, re.I)
        if m:  # ask Wikimedia for a 1200px rendition instead of a huge original
            src = f'{m.group(1)}/thumb/{m.group(2)}/{m.group(3)}/1200px-{m.group(3)}'
        img = None
        for u in ([src, orig] if orig != src else [src]):
            for _ in range(2):
                try:
                    img = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read(); break
                except Exception as e:
                    app.logger.warning('Download failed (%s): %s', u, e)
            if img: break
        if not img:
            return None
        if not (img.startswith(b'\xff\xd8') or img.startswith(b'\x89PNG')):
            return None  # not a real image
        ext = '.png' if img.startswith(b'\x89PNG') else '.jpg'
        fname = slug(name) + ext
        with open(os.path.join(IMG_DIR, fname), 'wb') as f:
            f.write(img)
        path = f'/static/cars/{fname}'
        with db() as c:
            c.execute('UPDATE cars SET image=? WHERE id=?', (path, car_id))
        return path
    except Exception as e:
        app.logger.warning('Image download failed for %s: %s', name, e)
        return None


def sync_car_images(force=False):
    with db() as c:
        rows = c.execute('SELECT id,name,category,wiki,image FROM cars').fetchall()
    for r in rows:
        real = r['image'] and r['image'].startswith('/static/cars/') and not r['image'].endswith('.svg') and os.path.exists(os.path.join(BASE, r['image'].lstrip('/')))
        if force or not real or local_car_image(r['name']) not in (None, r['image']):
            got = fetch_car_image(r['id'], r['name'], r['wiki'])
            print(f"  [photo] {r['name']}: {'OK ' + got if got else 'download failed - using named placeholder'}", flush=True)
        if not (r['image'] and os.path.exists(os.path.join(BASE, (r['image'] or 'x').lstrip('/')))) and not local_car_image(r['name']):
            with db() as c:
                if not c.execute('SELECT image FROM cars WHERE id=?', (r['id'],)).fetchone()['image'].endswith(('.jpg', '.png')):
                    c.execute('UPDATE cars SET image=? WHERE id=?', (make_car_svg(r['name'], r['category']), r['id']))


def ensure_images_async():
    threading.Thread(target=sync_car_images, daemon=True).start()


def user_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            flash('Please sign in or create an account to continue.', 'error')
            return redirect(url_for('user_login', next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return fn(*args, **kwargs)
    return wrapper


def razorpay_client():
    if not (razorpay and RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        return None
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


@app.before_request
def csrf_protect():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_hex(16)
    if request.method == 'POST' and request.endpoint != 'razorpay_webhook':
        tok = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        if not tok or not secrets.compare_digest(tok, session['csrf']):
            abort(400, 'Invalid or missing security token. Reload the page and try again.')


@app.context_processor
def common():
    return {'csrf_token': session.get('csrf', ''), 'today': date.today().isoformat(), 'admin_logged_in': bool(session.get('admin')), 'current_user': session.get('user_name'), 'user_logged_in': bool(session.get('user_id')), 'razorpay_key_id': RAZORPAY_KEY_ID}


@app.route('/')
def index():
    with db() as c:
        cars = c.execute('SELECT * FROM cars WHERE available=1 ORDER BY id DESC LIMIT 3').fetchall()
    return render_template('index.html', cars=cars)


_last_sync = [0]


@app.route('/cars')
def cars():
    import time
    if time.time() - _last_sync[0] > 600:  # retry missing photos at most every 10 min while people browse
        _last_sync[0] = time.time(); ensure_images_async()
    with db() as c:
        rows = c.execute('SELECT * FROM cars WHERE available=1 ORDER BY price').fetchall()
    return render_template('cars.html', cars=rows)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('user_id'):
        return redirect(url_for('my_bookings'))
    if request.method == 'POST':
        name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if len(name) < 2 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
            flash('Enter your full name and a valid email address.', 'error')
        elif not re.fullmatch(r'\d{10}', phone):
            flash('Enter a valid 10-digit mobile number.', 'error')
        elif len(password) < 8:
            flash('Password must contain at least 8 characters.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        else:
            try:
                with db() as c:
                    c.execute('INSERT INTO users(full_name,email,phone,password_hash) VALUES(?,?,?,?)', (name,email,phone,generate_password_hash(password)))
                    uid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
                session.clear(); session['user_id'] = uid; session['user_name'] = name; session['user_email'] = email
                flash('Account created successfully. Welcome to RentX!', 'success')
                return redirect(url_for('my_bookings'))
            except sqlite3.IntegrityError:
                flash('An account with that email already exists. Please sign in.', 'error')
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def user_login():
    if session.get('user_id'):
        return redirect(url_for('my_bookings'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        with db() as c:
            user = c.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session.clear(); session['user_id'] = user['id']; session['user_name'] = user['full_name']; session['user_email'] = user['email']
            flash('Welcome back!', 'success')
            nxt = request.args.get('next', '')
            return redirect(nxt if nxt.startswith('/') and not nxt.startswith('//') else url_for('my_bookings'))
        flash('Invalid email or password.', 'error')
    return render_template('login.html')


@app.route('/logout')
def user_logout():
    session.pop('user_id', None); session.pop('user_name', None); session.pop('user_email', None)
    flash('You have been signed out.', 'success')
    return redirect(url_for('index'))


@app.route('/my-bookings')
@user_required
def my_bookings():
    with db() as c:
        rows = c.execute('''SELECT b.*, cars.name AS car_name,cars.image AS car_image,cars.category AS car_category
                            FROM bookings b JOIN cars ON cars.id=b.car_id WHERE b.user_id=? ORDER BY b.id DESC''', (session['user_id'],)).fetchall()
    return render_template('my_bookings.html', bookings=rows)


@app.route('/booking', methods=['GET', 'POST'])
@user_required
def booking():
    with db() as c:
        rows = c.execute('SELECT * FROM cars WHERE available=1 ORDER BY name').fetchall()
        selected = c.execute('SELECT * FROM cars WHERE id=? AND available=1', (request.args.get('car', type=int),)).fetchone() if request.args.get('car') else None
        if request.method == 'POST':
            car_id = request.form.get('car_id', type=int)
            name = request.form.get('name', '').strip() or session.get('user_name', '')
            phone = request.form.get('phone', '').strip()
            email = request.form.get('email', '').strip() or session.get('user_email', '')
            car = c.execute('SELECT * FROM cars WHERE id=? AND available=1', (car_id,)).fetchone()
            unit = request.form.get('duration_unit', 'days')
            unit = unit if unit in ('hours', 'days') else 'days'
            val = request.form.get('duration_value', type=int) or 0
            start = request.form.get('start_date', '')
            stime = request.form.get('start_time', '10:00') or '10:00'
            try:
                start_dt = datetime.strptime(f'{start} {stime}', '%Y-%m-%d %H:%M')
            except ValueError:
                start_dt = None
            limit = 23 if unit == 'hours' else 60
            ok_dur = 1 <= val <= limit
            if start_dt and ok_dur:
                end_dt = start_dt + (timedelta(hours=val) if unit == 'hours' else timedelta(days=val))
                s_str, e_str = start_dt.strftime('%Y-%m-%d %H:%M'), end_dt.strftime('%Y-%m-%d %H:%M')
            clash = car and start_dt and ok_dur and c.execute('''SELECT 1 FROM bookings WHERE car_id=? AND COALESCE(start_dt,start_date||' 00:00')<? AND COALESCE(end_dt,end_date||' 00:00')>? AND (status IN ('Confirmed','Completed') OR (status='Pending Payment' AND created_at>?)) LIMIT 1''', (car_id, e_str, s_str, (datetime.utcnow()-timedelta(minutes=HOLD_MINUTES)).strftime('%Y-%m-%d %H:%M:%S'))).fetchone()
            if not car or not name or len(phone) != 10 or not phone.isdigit() or not start_dt or not ok_dur or start_dt < datetime.now() - timedelta(minutes=5):
                flash('Check your details, car, pick-up time and duration (1-23 hours or 1-60 days). Phone must be 10 digits and pick-up cannot be in the past.', 'error')
            elif clash:
                flash('Sorry, that car is already booked for part of that time. Please pick another time or car.', 'error')
            else:
                total = calc_total(car['price'], unit, val)
                days = max(1, math.ceil((end_dt - start_dt).total_seconds() / 86400))
                c.execute('''INSERT INTO bookings(car_id,name,phone,email,start_date,end_date,days,total,status,user_id,payment_status,start_dt,end_dt,duration_unit,duration_value)
                             VALUES(?,?,?,?,?,?,?,?,'Pending Payment',?,'Unpaid',?,?,?,?)''', (car_id,name,phone,email,start_dt.date().isoformat(),end_dt.date().isoformat(),days,total,session['user_id'],s_str,e_str,unit,val))
                booking_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
                return redirect(url_for('payment', booking_id=booking_id))
        me = c.execute('SELECT phone FROM users WHERE id=?', (session['user_id'],)).fetchone()
    return render_template('booking.html', cars=rows, selected=selected, my_phone=me['phone'] if me else '', hourly_factor=HOURLY_FACTOR)


def mark_paid(booking_id, order_id, payment_id, amount, method, source):
    """Idempotently record a successful payment (called by browser verify AND webhook)."""
    with db() as c:
        b = c.execute('SELECT total,payment_status FROM bookings WHERE id=?', (booking_id,)).fetchone()
        if not b or abs(float(b['total']) - float(amount)) > 0.01:
            app.logger.error('Amount mismatch for booking %s', booking_id)
            return False
        c.execute('INSERT OR IGNORE INTO payments(booking_id,order_id,payment_id,amount,status,method,source) VALUES(?,?,?,?,?,?,?)', (booking_id, order_id, payment_id, amount, 'captured', method, source))
        c.execute("UPDATE bookings SET payment_status='Paid',payment_id=?,status=CASE WHEN status='Cancelled' THEN status ELSE 'Confirmed' END WHERE id=?", (payment_id, booking_id))
    return True


def make_order(client, row, uid):
    order = client.order.create({'amount': int(round(float(row['total']) * 100)), 'currency': 'INR', 'receipt': f"rentx_{row['id']}_{secrets.token_hex(3)}", 'notes': {'booking_id': str(row['id']), 'user_id': str(uid)}})
    with db() as c:
        c.execute('UPDATE bookings SET payment_order_id=? WHERE id=?', (order['id'], row['id']))
    return order['id']


@app.route('/payment/<int:booking_id>')
@user_required
def payment(booking_id):
    with db() as c:
        row = c.execute('SELECT b.*, cars.name AS car_name,cars.image AS car_image FROM bookings b JOIN cars ON cars.id=b.car_id WHERE b.id=? AND b.user_id=?', (booking_id, session['user_id'])).fetchone()
    if not row:
        abort(404)
    if row['payment_status'].startswith('Paid'):
        return redirect(url_for('receipt', booking_id=booking_id))
    if row['status'] == 'Cancelled':
        flash('This booking was cancelled.', 'error'); return redirect(url_for('my_bookings'))
    client = razorpay_client(); order_id = row['payment_order_id']; row = dict(row)
    if client:
        try:
            # re-use the order only if it still matches the booking total
            if order_id:
                o = client.order.fetch(order_id)
                if o.get('amount') != int(round(row['total'] * 100)) or o.get('status') == 'paid':
                    order_id = None
            if not order_id:
                order_id = make_order(client, row, session['user_id'])
            row['payment_order_id'] = order_id
        except Exception:
            app.logger.exception('Could not create Razorpay order'); order_id = None
            flash('Payment service could not create an order. Check your Razorpay keys and internet connection.', 'error')
    # ---- real-time QR ----
    qr_mode, qr_data, qr_img, upi_url = 'none', None, None, None
    if client:
        try:
            qid = row.get('qr_id')
            if not qid:
                q = client.qrcode.create({'type': 'upi_qr', 'name': f"RentX RX-{booking_id:05d}", 'usage': 'single_use', 'fixed_amount': True, 'payment_amount': int(round(row['total'] * 100)), 'description': f"RentX booking RX-{booking_id:05d}", 'notes': {'booking_id': str(booking_id)}})
                qid = q['id']
                with db() as c:
                    c.execute('UPDATE bookings SET qr_id=? WHERE id=?', (qid, booking_id))
                row['qr_id'] = qid; row['_qr'] = q
            q = row.get('_qr') or client.qrcode.fetch(qid)
            qr_mode = 'razorpay'
            ic = q.get('image_content') or ''
            if ic.startswith('upi://') and len(ic) < 400:
                qr_data = ic  # a UPI link: render as our own QR
            elif ic.startswith('data:image') or ic.startswith('http'):
                qr_img = ic
            else:
                qr_img = q.get('image_url') or None
        except Exception:
            app.logger.warning('Razorpay QR unavailable (is QR Codes enabled on your account?)', exc_info=True)
    if qr_mode == 'none' and UPI_VPA:
        qr_mode = 'upi'; upi_url = upi_link(booking_id, row['total']); qr_data = upi_url
    if qr_mode == 'none' and not client:
        qr_mode = 'demo'
        if not row.get('pay_token'):
            tok = secrets.token_urlsafe(12)
            with db() as c:
                c.execute('UPDATE bookings SET pay_token=? WHERE id=?', (tok, booking_id))
            row['pay_token'] = tok
        qr_data = url_for('pay_scan', token=row['pay_token'], _external=True)
    try:
        qr = qr_svg(qr_data) if qr_data else None
    except Exception:
        app.logger.exception('QR render failed'); qr = None
    if qr_mode == 'razorpay' and not (qr or qr_img):
        qr_mode = 'none'  # QR unavailable: the Pay button / card checkout still works
    return render_template('payment.html', booking=row, gateway_ready=bool(client and order_id), demo_mode=(client is None and not UPI_VPA), key_id=RAZORPAY_KEY_ID, qr_mode=qr_mode, qr_svg=qr, qr_img=qr_img, upi_url=upi_url, upi_vpa=UPI_VPA)


@app.route('/pay/scan/<token>', methods=['GET', 'POST'])
def pay_scan(token):
    """DEMO ONLY: the page a phone opens after scanning the demo QR. Disabled once real keys / UPI ID exist."""
    if razorpay_client() or UPI_VPA:
        abort(404)
    with db() as c:
        b = c.execute('SELECT b.*,cars.name car_name FROM bookings b JOIN cars ON cars.id=b.car_id WHERE pay_token=?', (token,)).fetchone()
    if not b:
        abort(404)
    if request.method == 'POST' and not b['payment_status'].startswith('Paid') and b['status'] != 'Cancelled':
        with db() as c:
            c.execute("INSERT OR IGNORE INTO payments(booking_id,order_id,payment_id,amount,status,method,source) VALUES(?,?,?,?,?,?,?)", (b['id'], 'demo', f'demo_{secrets.token_hex(6)}', b['total'], 'demo', 'demo-qr', 'sandbox'))
            c.execute("UPDATE bookings SET payment_status='Paid (Demo)',status='Confirmed' WHERE id=?", (b['id'],))
        return redirect(url_for('pay_scan', token=token))
    return render_template('pay_scan.html', b=b)


@app.route('/payment/<int:booking_id>/utr', methods=['POST'])
@user_required
def payment_utr(booking_id):
    """Static UPI QR can't notify us, so the customer submits the UPI reference (UTR) and staff verify it."""
    utr = re.sub(r'\s', '', request.form.get('utr', ''))
    if not re.fullmatch(r'\d{12}', utr):
        flash('Enter the 12-digit UPI reference number (UTR) shown in your payment app.', 'error'); return redirect(url_for('payment', booking_id=booking_id))
    with db() as c:
        n = c.execute("UPDATE bookings SET utr=?,payment_status='Verification Pending' WHERE id=? AND user_id=? AND payment_status IN ('Unpaid','Verification Pending')", (utr, booking_id, session['user_id'])).rowcount
    flash('Thanks! We received your UPI reference and will confirm the booking after verifying it.' if n else 'Could not record that reference.', 'success' if n else 'error')
    return redirect(url_for('my_bookings'))


@app.route('/payment/verify', methods=['POST'])
@user_required
def payment_verify():
    booking_id = request.form.get('booking_id', type=int)
    payment_id = request.form.get('razorpay_payment_id', ''); order_id = request.form.get('razorpay_order_id', ''); signature = request.form.get('razorpay_signature', '')
    client = razorpay_client()
    if not (client and booking_id and payment_id and order_id and signature):
        flash('Payment verification details are incomplete. No payment was marked successful.', 'error'); return redirect(url_for('my_bookings'))
    with db() as c:
        row = c.execute('SELECT id,payment_order_id FROM bookings WHERE id=? AND user_id=?', (booking_id, session['user_id'])).fetchone()
    if not row or row['payment_order_id'] != order_id:
        abort(403)
    try:
        client.utility.verify_payment_signature({'razorpay_order_id': order_id, 'razorpay_payment_id': payment_id, 'razorpay_signature': signature})
        p = client.payment.fetch(payment_id)  # confirm with Razorpay servers, don't trust the browser alone
        if p.get('status') == 'authorized':
            p = client.payment.capture(payment_id, p['amount'], {'currency': p.get('currency', 'INR')})
        if p.get('status') == 'captured' and p.get('order_id') == order_id and mark_paid(booking_id, order_id, payment_id, p['amount'] / 100, p.get('method'), 'checkout'):
            flash('Payment successful! Your booking is confirmed.', 'success'); return redirect(url_for('receipt', booking_id=booking_id))
        flash('Payment is still processing. This page will update automatically once it is confirmed.', 'success')
    except Exception:
        app.logger.exception('Razorpay verification failed')
        flash('Payment could not be verified. If money was deducted it will be reconciled automatically; contact support with your booking number.', 'error')
    return redirect(url_for('my_bookings'))


@app.route('/webhooks/razorpay', methods=['POST'])
def razorpay_webhook():
    """Razorpay calls this server-to-server, so bookings confirm even if the customer closes the tab."""
    client = razorpay_client(); body = request.get_data(as_text=True)
    if not (client and RAZORPAY_WEBHOOK_SECRET):
        return {'ok': False, 'error': 'webhook not configured'}, 503
    try:
        client.utility.verify_webhook_signature(body, request.headers.get('X-Razorpay-Signature', ''), RAZORPAY_WEBHOOK_SECRET)
    except Exception:
        return {'ok': False}, 400
    evt = json.loads(body); pay = evt.get('payload', {}).get('payment', {}).get('entity', {})
    bid = (pay.get('notes') or {}).get('booking_id')
    qid = (evt.get('payload', {}).get('qr_code', {}).get('entity') or {}).get('id') or pay.get('qr_code_id')
    if not bid and qid:
        with db() as c:
            r = c.execute('SELECT id FROM bookings WHERE qr_id=?', (qid,)).fetchone(); bid = r['id'] if r else None
    if not bid and pay.get('order_id'):
        with db() as c:
            r = c.execute('SELECT id FROM bookings WHERE payment_order_id=?', (pay['order_id'],)).fetchone(); bid = r['id'] if r else None
    if evt.get('event') in ('payment.captured', 'order.paid', 'qr_code.credited') and bid:
        mark_paid(int(bid), pay.get('order_id'), pay.get('id'), pay.get('amount', 0) / 100, pay.get('method'), 'webhook')
    elif evt.get('event') == 'payment.failed' and bid:
        with db() as c:
            c.execute('INSERT OR IGNORE INTO payments(booking_id,order_id,payment_id,amount,status,method,source) VALUES(?,?,?,?,?,?,?)', (int(bid), pay.get('order_id'), pay.get('id'), pay.get('amount', 0) / 100, 'failed', pay.get('method'), 'webhook'))
    return {'ok': True}


@app.route('/payment/status/<int:booking_id>')
@user_required
def payment_status(booking_id):
    """Polled by the browser for real-time payment/booking status."""
    with db() as c:
        r = c.execute('SELECT status,payment_status,payment_order_id FROM bookings WHERE id=? AND user_id=?', (booking_id, session['user_id'])).fetchone()
    if not r:
        abort(404)
    client = razorpay_client()
    if client and not r['payment_status'].startswith('Paid'):  # reconcile with Razorpay if the webhook is delayed
        try:
            with db() as c:
                qid = c.execute('SELECT qr_id FROM bookings WHERE id=?', (booking_id,)).fetchone()['qr_id']
            if qid:
                for p in client.qrcode.fetch_all_payments(qid).get('items', []):
                    if p.get('status') == 'captured':
                        mark_paid(booking_id, p.get('order_id') or qid, p['id'], p['amount'] / 100, p.get('method', 'upi'), 'qr')
                        return {'status': 'Confirmed', 'payment_status': 'Paid'}
        except Exception:
            app.logger.warning('QR reconcile failed')
        try:
            if not r['payment_order_id']:
                raise RuntimeError
            for p in client.order.payments(r['payment_order_id']).get('items', []):
                if p.get('status') == 'captured':
                    mark_paid(booking_id, p['order_id'], p['id'], p['amount'] / 100, p.get('method'), 'poll')
                    return {'status': 'Confirmed', 'payment_status': 'Paid'}
        except Exception:
            app.logger.warning('status reconcile failed')
    return {'status': r['status'], 'payment_status': r['payment_status']}


@app.route('/payment/<int:booking_id>/demo', methods=['POST'])
@user_required
def payment_demo(booking_id):
    """Sandbox only. Works ONLY when no Razorpay keys are configured; no real money is involved."""
    if razorpay_client() or UPI_VPA:
        abort(404)
    with db() as c:
        row = c.execute('SELECT * FROM bookings WHERE id=? AND user_id=?', (booking_id, session['user_id'])).fetchone()
        if not row:
            abort(404)
        c.execute("INSERT OR IGNORE INTO payments(booking_id,order_id,payment_id,amount,status,method,source) VALUES(?,?,?,?,?,?,?)", (booking_id, 'demo', f'demo_{secrets.token_hex(6)}', row['total'], 'demo', 'demo-card', 'sandbox'))
        c.execute("UPDATE bookings SET payment_status='Paid (Demo)',status='Confirmed' WHERE id=?", (booking_id,))
    flash('Demo payment recorded. No real money was charged.', 'success')
    return redirect(url_for('receipt', booking_id=booking_id))


@app.route('/receipt/<int:booking_id>')
@user_required
def receipt(booking_id):
    with db() as c:
        b = c.execute('SELECT b.*,cars.name car_name,cars.image car_image,cars.registration reg FROM bookings b JOIN cars ON cars.id=b.car_id WHERE b.id=? AND b.user_id=?', (booking_id, session['user_id'])).fetchone()
        pay = c.execute("SELECT * FROM payments WHERE booking_id=? AND status IN ('captured','demo') ORDER BY id DESC LIMIT 1", (booking_id,)).fetchone()
    if not b:
        abort(404)
    try:
        qr = qr_svg(f"RENTX|RX-{b['id']:05d}|{b['car_name']}|{b['total']:.2f}", 4) or ''
    except Exception:
        app.logger.exception('Receipt QR failed'); qr = ''
    return render_template('booking_success.html', b=b, pay=pay, qr=qr)


_PDF_FONTS = {}


def pdf_fonts():
    """Register a Unicode font (bundled DejaVu Sans) so Rs/₹ and accented names print; fall back to Helvetica."""
    if _PDF_FONTS:
        return _PDF_FONTS['r'], _PDF_FONTS['b']
    r, b = 'Helvetica', 'Helvetica-Bold'
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        fdir = os.path.join(BASE, 'static', 'fonts')
        pdfmetrics.registerFont(TTFont('RentXSans', os.path.join(fdir, 'DejaVuSans.ttf')))
        pdfmetrics.registerFont(TTFont('RentXSans-Bold', os.path.join(fdir, 'DejaVuSans-Bold.ttf')))
        r, b = 'RentXSans', 'RentXSans-Bold'
    except Exception:
        app.logger.warning('Unicode PDF font unavailable, using Helvetica', exc_info=True)
    _PDF_FONTS.update(r=r, b=b)
    return r, b


def build_invoice_pdf(b, pay):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.graphics.barcode import qr as rlqr
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF
    from reportlab.pdfbase.pdfmetrics import stringWidth
    FR, FB = pdf_fonts()
    uni = FR != 'Helvetica'
    def T(v, font=None, size=10, maxw=None):
        """Make any value safe to print: str, glyph-safe, and shortened to fit."""
        v = '' if v is None else str(v)
        if not uni:
            v = v.encode('latin-1', 'replace').decode('latin-1')
        else:
            from reportlab.pdfbase import pdfmetrics
            cmap = pdfmetrics.getFont(FR).face.charToGlyph
            v = ''.join(ch if (ch in '\n ' or ord(ch) in cmap) else '?' for ch in v)
        if maxw:
            while len(v) > 1 and stringWidth(v, font or FR, size) > maxw:
                v = v[:-2] + '…' if uni else v[:-1]
        return v
    buf = io.BytesIO(); W, H = A4; p = canvas.Canvas(buf, pagesize=A4)
    gold = colors.HexColor('#b8862b'); ink = colors.HexColor('#17191c'); grey = colors.HexColor('#6b7078')
    s, e, unit, val = booking_times(b)
    ref = f"RX-{b['id']:05d}"
    p.setFillColor(ink); p.rect(0, H - 38*mm, W, 38*mm, stroke=0, fill=1)
    p.setFillColor(gold); p.setFont(FB, 26); p.drawString(20*mm, H - 22*mm, 'RentX')
    p.setFillColor(colors.white); p.setFont(FR, 9); p.drawString(20*mm, H - 29*mm, 'Premium car rentals')
    p.setFont(FB, 16); p.drawRightString(W - 20*mm, H - 20*mm, 'INVOICE / BILL')
    p.setFont(FR, 9); p.drawRightString(W - 20*mm, H - 27*mm, f'Invoice no: INV-{ref}')
    paid_on = (pay['created_at'] if pay else b['created_at']) or ''
    p.drawRightString(W - 20*mm, H - 32*mm, f'Date: {T(paid_on[:16])} UTC')
    y = H - 55*mm
    def block(x, title, lines):
        nonlocal y
        p.setFillColor(gold); p.setFont(FB, 8); p.drawString(x, y, title)
        p.setFillColor(ink); p.setFont(FR, 10)
        for i, t in enumerate(lines):
            p.drawString(x, y - (5 + i * 5) * mm, T(t, FR, 10, 80*mm))
    block(20*mm, 'BILLED TO', [b['name'], f"Phone: {b['phone']}", f"Email: {b['email'] or '-'}"])
    block(115*mm, 'VEHICLE', [b['car_name'], f"Reg. no: {b['reg']}", f"Booking ref: {ref}"])
    y -= 32*mm
    p.setFillColor(gold); p.setFont(FB, 8); p.drawString(20*mm, y, 'RENTAL PERIOD')
    p.setFillColor(ink); p.setFont(FR, 10)
    p.drawString(20*mm, y - 5*mm, f'Pick-up : {T(fmt_dt(s))}'); p.drawString(20*mm, y - 10*mm, f'Return  : {T(fmt_dt(e))}'); p.drawString(20*mm, y - 15*mm, f'Duration: {duration_label(unit, val)}')
    y -= 28*mm
    p.setFillColor(ink); p.rect(20*mm, y, W - 40*mm, 8*mm, stroke=0, fill=1); p.setFillColor(colors.white); p.setFont(FB, 9)
    p.drawString(23*mm, y + 2.5*mm, 'DESCRIPTION'); p.drawRightString(W - 23*mm, y + 2.5*mm, 'AMOUNT (INR)')
    p.setFillColor(ink); p.setFont(FR, 10); y -= 8*mm
    p.drawString(23*mm, y + 1*mm, T(f"{b['car_name']} rental - {duration_label(unit, val)}", FR, 10, 110*mm)); p.drawRightString(W - 23*mm, y + 1*mm, f"{b['total']:,.2f}")
    p.setStrokeColor(colors.HexColor('#d9d9d9')); p.line(20*mm, y - 2*mm, W - 20*mm, y - 2*mm)
    y -= 12*mm; p.setFont(FB, 13); p.drawString(23*mm, y, 'TOTAL PAID'); p.drawRightString(W - 23*mm, y, (f"₹ {b['total']:,.2f}" if uni else f"Rs. {b['total']:,.2f}"))
    p.setFont(FR, 8); p.setFillColor(grey); p.drawString(23*mm, y - 5*mm, 'Amount is inclusive of all applicable taxes.')
    y -= 22*mm; p.setFillColor(gold); p.setFont(FB, 8); p.drawString(20*mm, y, 'PAYMENT DETAILS'); p.setFillColor(ink); p.setFont(FR, 10)
    rows = [('Status', 'PAID' + (' (DEMO - no real money)' if str(b['payment_status']).endswith('(Demo)') else '')), ('Method', (pay['method'] or 'online').upper() if pay else (b['utr'] and 'UPI') or '-'), ('Payment ID', (pay['payment_id'] if pay else b['payment_id']) or '-')]
    if b['utr']: rows.append(('UPI ref (UTR)', b['utr']))
    for i, (k, v) in enumerate(rows):
        p.drawString(20*mm, y - (5 + i * 5) * mm, f'{k}:'); p.drawString(50*mm, y - (5 + i * 5) * mm, T(v, FR, 10, 80*mm))
    w = rlqr.QrCodeWidget(f"RENTX|{ref}|{b['car_name']}|{s}|{e}|INR{b['total']:.2f}|{(pay['payment_id'] if pay else b['payment_id']) or ''}"[:300], barLevel='L')
    x0, y0, x1, y1 = w.getBounds(); size = 34*mm
    d = Drawing(size, size, transform=[size/(x1-x0), 0, 0, size/(y1-y0), 0, 0]); d.add(w)
    renderPDF.draw(d, p, W - 20*mm - size, y - 22*mm)
    p.setFont(FR, 7); p.setFillColor(grey); p.drawRightString(W - 20*mm, y - 26*mm, 'Scan to verify booking')
    p.setFont(FR, 8); p.drawCentredString(W / 2, 15*mm, 'Thank you for choosing RentX. Carry a valid driving licence and this bill at pick-up. This is a computer-generated invoice.')
    p.showPage(); p.save(); buf.seek(0)
    return buf.read()


def _invoice_response(booking_id, user_id=None):
    q = 'SELECT b.*,cars.name car_name,cars.registration reg FROM bookings b JOIN cars ON cars.id=b.car_id WHERE b.id=?'
    args = [booking_id]
    if user_id is not None:
        q += ' AND b.user_id=?'; args.append(user_id)
    with db() as c:
        b = c.execute(q, args).fetchone()
        pay = c.execute("SELECT * FROM payments WHERE booking_id=? AND status IN ('captured','demo') ORDER BY id DESC LIMIT 1", (booking_id,)).fetchone()
    if not b:
        abort(404)
    if not str(b['payment_status']).startswith('Paid'):
        return None, b
    pdf = build_invoice_pdf(b, pay)
    if not pdf.startswith(b'%PDF'):
        raise RuntimeError('invoice builder returned invalid data')
    return Response(pdf, mimetype='application/pdf', headers={'Content-Disposition': f'inline; filename=RentX-Invoice-RX-{booking_id:05d}.pdf', 'Cache-Control': 'no-store'}), b


@app.route('/invoice/<int:booking_id>.pdf')
@user_required
def invoice(booking_id):
    try:
        resp, b = _invoice_response(booking_id, session['user_id'])
    except Exception:
        app.logger.exception('Invoice PDF generation failed for booking %s', booking_id)
        flash('The PDF could not be generated right now (is reportlab installed? run: pip install -r requirements.txt). Your on-screen bill is shown instead; you can print it.', 'error')
        return redirect(url_for('receipt', booking_id=booking_id))
    if resp is None:
        flash('The bill is available after payment is confirmed.', 'error'); return redirect(url_for('payment', booking_id=booking_id))
    return resp


@app.route('/admin/invoice/<int:booking_id>.pdf')
@admin_required
def admin_invoice(booking_id):
    try:
        resp, b = _invoice_response(booking_id)
    except Exception:
        app.logger.exception('Admin invoice failed for booking %s', booking_id)
        flash('Invoice PDF could not be generated. Check the server log.', 'error'); return redirect(url_for('admin_dashboard') + '#bookings')
    if resp is None:
        flash('That booking is not paid yet.', 'error'); return redirect(url_for('admin_dashboard') + '#bookings')
    return resp


@app.route('/admin/bookings/<int:booking_id>/mark-paid', methods=['POST'])
@admin_required
def admin_mark_paid(booking_id):
    with db() as c:
        b = c.execute('SELECT * FROM bookings WHERE id=?', (booking_id,)).fetchone()
        if b and b['status'] != 'Cancelled' and not str(b['payment_status']).startswith('Paid'):
            c.execute('INSERT OR IGNORE INTO payments(booking_id,order_id,payment_id,amount,status,method,source) VALUES(?,?,?,?,?,?,?)', (booking_id, 'upi-qr', f"upi_{b['utr'] or secrets.token_hex(5)}", b['total'], 'captured', 'upi', 'admin-verified'))
            c.execute("UPDATE bookings SET payment_status='Paid',status='Confirmed',payment_id=? WHERE id=?", (f"upi_{b['utr'] or booking_id}", booking_id))
    flash('Payment verified and booking confirmed.', 'success'); return redirect(url_for('admin_dashboard') + '#bookings')


@app.route('/admin/cars/<int:car_id>/image', methods=['POST'])
@admin_required
def upload_car_image(car_id):
    f = request.files.get('photo')
    with db() as c:
        car = c.execute('SELECT name FROM cars WHERE id=?', (car_id,)).fetchone()
    if not car or not f or not f.filename.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
        flash('Choose a JPG, PNG or WEBP photo.', 'error'); return redirect(url_for('admin_dashboard') + '#fleet')
    data = f.read()
    if len(data) > 5_000_000:
        flash('Photo must be under 5 MB.', 'error'); return redirect(url_for('admin_dashboard') + '#fleet')
    ext = os.path.splitext(f.filename.lower())[1]
    fname = slug(car['name']) + ext
    with open(os.path.join(IMG_DIR, fname), 'wb') as out:
        out.write(data)
    with db() as c:
        c.execute('UPDATE cars SET image=? WHERE id=?', (f'/static/cars/{fname}', car_id))
    flash(f"Photo updated for {car['name']}.", 'success'); return redirect(url_for('admin_dashboard') + '#fleet')


@app.route('/admin/cars/refresh-images', methods=['POST'])
@admin_required
def refresh_images():
    threading.Thread(target=sync_car_images, kwargs={'force': True}, daemon=True).start()
    flash('Re-downloading real car photos in the background. Reload in a minute.', 'success'); return redirect(url_for('admin_dashboard') + '#fleet')


@app.route('/bookings/<int:booking_id>/cancel', methods=['POST'])
@user_required
def cancel_booking(booking_id):
    with db() as c:
        n = c.execute("UPDATE bookings SET status='Cancelled' WHERE id=? AND user_id=? AND payment_status='Unpaid'", (booking_id, session['user_id'])).rowcount
    flash('Booking cancelled.' if n else 'Paid bookings cannot be cancelled online. Please contact support for a refund.', 'success' if n else 'error')
    return redirect(url_for('my_bookings'))


@app.route('/about')
def about(): return render_template('about.html')


@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name=request.form.get('name','').strip(); email=request.form.get('email','').strip(); subject=request.form.get('subject','').strip(); message=request.form.get('message','').strip()
        if name and email and message:
            with db() as c: c.execute('INSERT INTO messages(name,email,subject,message) VALUES(?,?,?,?)',(name,email,subject,message))
            flash('Thanks! Your message has been saved. Our team can follow up soon.','success'); return redirect(url_for('contact'))
        flash('Please fill in your name, email, and message.','error')
    return render_template('contact.html')


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('username') == ADMIN_USER and request.form.get('password') == ADMIN_PASS:
            session['admin'] = True; return redirect(url_for('admin_dashboard'))
        flash('Incorrect username or password.','error')
    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout(): session.clear(); return redirect(url_for('index'))


@app.route('/admin')
@admin_required
def admin_dashboard():
    with db() as c:
        stats={'cars':c.execute('SELECT COUNT(*) FROM cars').fetchone()[0], 'available':c.execute('SELECT COUNT(*) FROM cars WHERE available=1').fetchone()[0], 'bookings':c.execute('SELECT COUNT(*) FROM bookings').fetchone()[0], 'revenue':c.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='captured'").fetchone()[0]}
        bookings=c.execute('SELECT b.*,c.name car_name FROM bookings b JOIN cars c ON c.id=b.car_id ORDER BY b.id DESC LIMIT 50').fetchall()
        cars=c.execute('SELECT * FROM cars ORDER BY id DESC').fetchall()
        messages=c.execute('SELECT * FROM messages ORDER BY id DESC LIMIT 5').fetchall()
    return render_template('admin_dashboard.html',stats=stats,bookings=bookings,cars=cars,messages=messages)


@app.route('/admin/cars/add', methods=['POST'])
@admin_required
def add_car():
    name=request.form.get('name','').strip(); reg=request.form.get('registration','').strip().upper(); cat=request.form.get('category','Standard'); image=request.form.get('image','').strip(); desc=request.form.get('description','').strip()
    try: price=float(request.form.get('price','0'))
    except ValueError: price=0
    if not name or not reg or price<=0: flash('Enter a car name, unique registration, and a price above zero.','error')
    else:
        try:
            with db() as c:
                c.execute('INSERT INTO cars(name,registration,category,price,image,description) VALUES(?,?,?,?,?,?)',(name,reg,cat,price,image,desc)); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            if not image: threading.Thread(target=fetch_car_image,args=(cid,name),daemon=True).start()
            flash('Vehicle added to the fleet successfully.','success')
        except sqlite3.IntegrityError: flash('That registration number already exists.','error')
    return redirect(url_for('admin_dashboard')+'#fleet')


@app.route('/admin/cars/<int:car_id>/toggle', methods=['POST'])
@admin_required
def toggle_car(car_id):
    with db() as c: c.execute('UPDATE cars SET available=CASE available WHEN 1 THEN 0 ELSE 1 END WHERE id=?',(car_id,))
    flash('Vehicle availability updated.','success'); return redirect(url_for('admin_dashboard')+'#fleet')


@app.route('/admin/bookings/<int:booking_id>/status', methods=['POST'])
@admin_required
def booking_status(booking_id):
    status=request.form.get('status','Confirmed')
    if status in ('Confirmed','Completed','Cancelled','Pending Payment'):
        with db() as c: c.execute('UPDATE bookings SET status=? WHERE id=?',(status,booking_id))
        flash('Booking status updated.','success')
    return redirect(url_for('admin_dashboard')+'#bookings')


if __name__ == '__main__':
    init_db()
    print('Fetching real car photos (needs internet; falls back to named placeholders)...', flush=True)
    sync_car_images()  # downloads real car photos before the site starts
    app.run(host=os.environ.get('RENTX_HOST', '127.0.0.1'), port=5000, debug=False, use_reloader=False)
