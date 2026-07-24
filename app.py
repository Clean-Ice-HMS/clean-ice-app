#!/usr/bin/env python3
"""
Clean Ice West Scotland - Cloud Version
PostgreSQL + Supabase deployment
"""

from flask import Flask, render_template, request, redirect, url_for, make_response
import psycopg
import psycopg.rows
import os
import csv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dateutil.relativedelta import relativedelta

app = Flask(__name__)

# Database connection from environment variable
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:[YOUR-PASSWORD]@db.tfxkrobotgyiztikzstp.supabase.co:5432/postgres')

EMAIL_CONFIG = {
    'smtp_server': 'smtp.gmail.com',
    'smtp_port': 587,
    'use_tls': True,
    'sender_email': '',
    'sender_password': '',
    'sender_name': 'Clean Ice West Scotland'
}

def get_db():
    conn = psycopg.connect(DATABASE_URL)
    conn.autocommit = False
    return conn

def calculate_next_due(last_visit_date, interval_months):
    if not last_visit_date:
        return None
    if isinstance(last_visit_date, str):
        last_date = datetime.strptime(last_visit_date[:10], '%Y-%m-%d')
    else:
        last_date = last_visit_date
    next_date = last_date + relativedelta(months=interval_months)
    return next_date.strftime('%Y-%m-%d')

@app.route('/')
def dashboard():
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    
    cur.execute('SELECT COUNT(*) FROM customers')
    stats = {
        'customers': cur.fetchone()['count'],
    }
    cur.execute('SELECT COUNT(*) FROM assets WHERE status=%s', ('active',))
    stats['assets'] = cur.fetchone()['count']
    cur.execute('SELECT COUNT(*) FROM bookings')
    stats['bookings'] = cur.fetchone()['count']
    stats['pass_rate'] = 0
    
    cur.execute("SELECT ROUND(SUM(CASE WHEN pass_fail=%s THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(*), 0) * 100, 1) FROM atp_readings", ('pass',))
    row = cur.fetchone()
    stats['pass_rate'] = row['round'] if row and row['round'] else 0
    
    cur.execute('''
        SELECT a.*, c.business_name,
            (SELECT MAX(b.visit_date) FROM booking_assets ba 
             JOIN bookings b ON ba.booking_id = b.id 
             WHERE ba.asset_id = a.id) as last_visit,
            a.cleaning_interval_months
        FROM assets a
        JOIN customers c ON a.customer_id = c.id
        WHERE a.status = 'active'
    ''')
    upcoming_due = cur.fetchall()
    
    due_soon = []
    overdue = []
    for asset in upcoming_due:
        if asset['last_visit'] and asset['cleaning_interval_months']:
            next_due = calculate_next_due(asset['last_visit'], asset['cleaning_interval_months'])
            if next_due:
                days_until = (datetime.strptime(next_due, '%Y-%m-%d') - datetime.now()).days
                asset_dict = dict(asset)
                asset_dict['next_due'] = next_due
                asset_dict['days_until'] = days_until
                if days_until < 0:
                    overdue.append(asset_dict)
                elif days_until <= 30:
                    due_soon.append(asset_dict)
    
    cur.execute('''
        SELECT b.*, c.business_name, e.name as engineer_name,
            STRING_AGG(a.machine_make || ' ' || a.machine_model, ', ') as machines
        FROM bookings b
        JOIN customers c ON b.customer_id = c.id
        LEFT JOIN engineers e ON b.engineer_id = e.id
        LEFT JOIN booking_assets ba ON b.id = ba.booking_id
        LEFT JOIN assets a ON ba.asset_id = a.id
        GROUP BY b.id, c.business_name, e.name
        ORDER BY b.visit_date DESC LIMIT 5
    ''')
    recent_bookings = cur.fetchall()
    
    conn.close()
    return render_template('dashboard.html', stats=stats, recent_bookings=recent_bookings,
                         due_soon=due_soon, overdue=overdue)

@app.route('/customers')
def customers():
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM customers WHERE status='active' ORDER BY business_name")
    customers = cur.fetchall()
    conn.close()
    return render_template('customers.html', customers=customers)

@app.route('/customers/<int:id>')
def customer_detail(id):
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute('SELECT * FROM customers WHERE id=%s', (id,))
    customer = cur.fetchone()
    cur.execute('SELECT * FROM assets WHERE customer_id=%s', (id,))
    assets = cur.fetchall()
    cur.execute('''
        SELECT b.*, e.name as engineer_name,
            STRING_AGG(a.machine_make || ' ' || a.machine_model, ', ') as machines
        FROM bookings b
        LEFT JOIN engineers e ON b.engineer_id = e.id
        LEFT JOIN booking_assets ba ON b.id = ba.booking_id
        LEFT JOIN assets a ON ba.asset_id = a.id
        WHERE b.customer_id=%s
        GROUP BY b.id, e.name
        ORDER BY b.visit_date DESC
    ''', (id,))
    bookings = cur.fetchall()
    conn.close()
    return render_template('customer_detail.html', customer=customer, assets=assets, bookings=bookings)

@app.route('/customers/new', methods=['GET', 'POST'])
def customer_new():
    if request.method == 'POST':
        conn = get_db()
        cur = conn.cursor()
        status = request.form.get('status', 'active')
        cur.execute('INSERT INTO customers (business_name, contact_name, phone, email, address, postcode, status) VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (request.form['business_name'], request.form['contact_name'], request.form['phone'],
             request.form['email'], request.form['address'], request.form['postcode'], status))
        conn.commit()
        conn.close()
        return redirect('/customers')
    return render_template('customer_form.html')

@app.route('/customers/<int:id>/edit', methods=['GET', 'POST'])
def customer_edit(id):
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    if request.method == 'POST':
        status = request.form.get('status', 'active')
        cur.execute('''UPDATE customers SET 
            business_name=%s, contact_name=%s, phone=%s, email=%s, 
            address=%s, postcode=%s, status=%s WHERE id=%s''',
            (request.form['business_name'], request.form['contact_name'],
             request.form['phone'], request.form['email'],
             request.form['address'], request.form['postcode'], status, id))
        conn.commit()
        conn.close()
        return redirect(f'/customers/{id}')
    cur.execute('SELECT * FROM customers WHERE id=%s', (id,))
    customer = cur.fetchone()
    conn.close()
    return render_template('customer_form.html', customer=customer)

@app.route('/assets')
def assets():
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute('''
        SELECT a.*, c.business_name,
            (SELECT MAX(b.visit_date) FROM booking_assets ba 
             JOIN bookings b ON ba.booking_id = b.id 
             WHERE ba.asset_id = a.id) as last_visit
        FROM assets a
        JOIN customers c ON a.customer_id = c.id
        ORDER BY c.business_name, a.machine_make
    ''')
    assets = cur.fetchall()
    cur.execute('SELECT id, business_name FROM customers ORDER BY business_name')
    customers = cur.fetchall()
    conn.close()
    return render_template('assets.html', assets=assets, customers=customers)

@app.route('/assets/new', methods=['POST'])
def asset_new():
    if request.method == 'POST':
        conn = get_db()
        cur = conn.cursor()
        interval = request.form.get('cleaning_interval_months', 6)
        manufacture_date = request.form.get('manufacture_date') or None
        power_rating = request.form.get('power_rating', '')
        cur.execute('''INSERT INTO assets 
            (customer_id, machine_make, machine_model, serial_number, location_notes, install_date, cleaning_interval_months, manufacture_date, power_rating) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)''',
            (request.form['customer_id'], request.form['machine_make'], request.form['machine_model'],
             request.form['serial_number'], request.form['location_notes'], request.form['install_date'], 
             interval, manufacture_date, power_rating))
        conn.commit()
        conn.close()
        return redirect('/assets')
    return redirect('/assets')

@app.route('/assets/<int:id>/update-interval', methods=['POST'])
def update_interval(id):
    conn = get_db()
    cur = conn.cursor()
    interval = request.form.get('cleaning_interval_months', 6)
    cur.execute('UPDATE assets SET cleaning_interval_months=%s WHERE id=%s', (interval, id))
    conn.commit()
    conn.close()
    return redirect('/assets')

@app.route('/assets/<int:id>/edit', methods=['GET', 'POST'])
def asset_edit(id):
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    if request.method == 'POST':
        status = request.form.get('status', 'active')
        cur.execute('''UPDATE assets SET 
            customer_id=%s, machine_make=%s, machine_model=%s, serial_number=%s,
            location_notes=%s, install_date=%s, cleaning_interval_months=%s,
            manufacture_date=%s, power_rating=%s, status=%s WHERE id=%s''',
            (request.form['customer_id'], request.form['machine_make'],
             request.form['machine_model'], request.form['serial_number'],
             request.form['location_notes'], request.form['install_date'],
             request.form.get('cleaning_interval_months', 6),
             request.form.get('manufacture_date') or None,
             request.form.get('power_rating', ''), status, id))
        conn.commit()
        conn.close()
        return redirect('/assets')
    cur.execute('SELECT * FROM assets WHERE id=%s', (id,))
    asset = cur.fetchone()
    cur.execute('SELECT id, business_name FROM customers ORDER BY business_name')
    customers = cur.fetchall()
    conn.close()
    return render_template('asset_form.html', asset=asset, customers=customers)

@app.route('/bookings')
def bookings():
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute('''
        SELECT b.*, c.business_name, e.name as engineer_name,
            STRING_AGG(a.machine_make || ' ' || a.machine_model, ', ') as machines,
            COUNT(ba.id) as machine_count
        FROM bookings b
        JOIN customers c ON b.customer_id = c.id
        LEFT JOIN engineers e ON b.engineer_id = e.id
        LEFT JOIN booking_assets ba ON b.id = ba.booking_id
        LEFT JOIN assets a ON ba.asset_id = a.id
        GROUP BY b.id, c.business_name, e.name
        ORDER BY b.visit_date DESC
    ''')
    bookings = cur.fetchall()
    cur.execute('SELECT id, business_name FROM customers ORDER BY business_name')
    customers = cur.fetchall()
    cur.execute('SELECT id, name FROM engineers WHERE active=1')
    engineers = cur.fetchall()
    conn.close()
    return render_template('bookings.html', bookings=bookings, customers=customers, engineers=engineers)

@app.route('/bookings/assets/<int:customer_id>')
def get_customer_assets(customer_id):
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute('SELECT id, machine_make, machine_model, serial_number FROM assets WHERE customer_id=%s AND status=%s', (customer_id, 'active'))
    assets = cur.fetchall()
    conn.close()
    return {'assets': [dict(a) for a in assets]}

@app.route('/bookings/new', methods=['POST'])
def booking_new():
    if request.method == 'POST':
        conn = get_db()
        cur = conn.cursor()
        customer_id = request.form['customer_id']
        engineer_id = request.form['engineer_id']
        visit_date = request.form['visit_date']
        notes = request.form.get('notes', '')
        status = request.form.get('status', 'completed')
        
        cur.execute('''INSERT INTO bookings 
            (customer_id, engineer_id, visit_date, notes, status) 
            VALUES (%s, %s, %s, %s, %s) RETURNING id''',
            (customer_id, engineer_id, visit_date, notes, status))
        booking_id = cur.fetchone()[0]
        
        asset_ids = request.form.getlist('asset_ids')
        for asset_id in asset_ids:
            if asset_id:
                cur.execute('''INSERT INTO booking_assets 
                    (booking_id, asset_id, work_completed, sanitisation_done, overall_condition, notes) 
                    VALUES (%s, %s, %s, %s, %s, %s)''',
                    (booking_id, asset_id,
                     request.form.get('work_completed', ''),
                     1 if 'sanitisation_done' in request.form else 0,
                     request.form.get('overall_condition', 'good'),
                     request.form.get('asset_notes', '')))
        
        conn.commit()
        conn.close()
        return redirect(f'/bookings/{booking_id}')
    return redirect('/bookings')

@app.route('/bookings/<int:id>')
def booking_detail(id):
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    
    cur.execute('''
        SELECT b.*, c.business_name, c.contact_name, c.phone, c.email, c.address, c.postcode,
               e.name as engineer_name
        FROM bookings b
        JOIN customers c ON b.customer_id = c.id
        LEFT JOIN engineers e ON b.engineer_id = e.id
        WHERE b.id=%s
    ''', (id,))
    booking = cur.fetchone()
    
    if not booking:
        conn.close()
        return redirect('/bookings')
    
    cur.execute('''
        SELECT ba.*, a.machine_make, a.machine_model, a.serial_number, a.location_notes, a.cleaning_interval_months
        FROM booking_assets ba
        JOIN assets a ON ba.asset_id = a.id
        WHERE ba.booking_id=%s
    ''', (id,))
    assets = cur.fetchall()
    
    asset_data = []
    for asset in assets:
        cur.execute('SELECT * FROM atp_readings WHERE booking_asset_id=%s', (asset['id'],))
        readings = cur.fetchall()
        next_due = calculate_next_due(booking['visit_date'], asset['cleaning_interval_months'])
        overall_result = 'PASS'
        for r in readings:
            if r['pass_fail'] == 'fail':
                overall_result = 'FAIL'
                break
        asset_data.append({
            'asset': asset,
            'readings': readings,
            'next_due': next_due,
            'overall_result': overall_result
        })
    
    conn.close()
    return render_template('booking_detail.html', booking=booking, asset_data=asset_data)

@app.route('/bookings/<int:id>/certificate/<int:ba_id>')
def certificate(id, ba_id):
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    
    cur.execute('''
        SELECT b.*, c.business_name, c.contact_name, c.address, c.postcode, c.email,
               e.name as engineer_name
        FROM bookings b
        JOIN customers c ON b.customer_id = c.id
        LEFT JOIN engineers e ON b.engineer_id = e.id
        WHERE b.id=%s
    ''', (id,))
    booking = cur.fetchone()
    
    cur.execute('''
        SELECT ba.*, a.machine_make, a.machine_model, a.serial_number, a.location_notes, a.cleaning_interval_months
        FROM booking_assets ba
        JOIN assets a ON ba.asset_id = a.id
        WHERE ba.id=%s
    ''', (ba_id,))
    asset = cur.fetchone()
    
    if not booking or not asset:
        conn.close()
        return redirect('/bookings')
    
    cur.execute('SELECT * FROM atp_readings WHERE booking_asset_id=%s', (ba_id,))
    readings = cur.fetchall()
    next_due = calculate_next_due(booking['visit_date'], asset['cleaning_interval_months'])
    
    overall_result = 'PASS'
    for reading in readings:
        if reading['pass_fail'] == 'fail':
            overall_result = 'FAIL'
            break
    
    cur.execute('''
        SELECT a.machine_make, a.machine_model, a.serial_number
        FROM booking_assets ba
        JOIN assets a ON ba.asset_id = a.id
        WHERE ba.booking_id=%s
    ''', (id,))
    all_machines = cur.fetchall()
    
    conn.close()
    return render_template('certificate.html', booking=booking, asset=asset, readings=readings,
                         next_due=next_due, overall_result=overall_result,
                         issue_date=datetime.now().strftime('%d %B %Y'),
                         all_machines=all_machines, booking_id=id)

@app.route('/bookings/<int:id>/certificate/<int:ba_id>/email', methods=['GET', 'POST'])
def certificate_email(id, ba_id):
    """Send certificate via email using Brevo API"""
    import os
    import requests
    
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    
    # Get booking and customer details
    cur.execute('''
        SELECT b.*, c.business_name, c.contact_name, c.email, c.address, c.postcode
        FROM bookings b
        JOIN customers c ON b.customer_id = c.id
        WHERE b.id=%s
    ''', (id,))
    booking = cur.fetchone()
    
    if not booking or not booking.get('email'):
        conn.close()
        return redirect(f'/bookings/{id}/certificate/{ba_id}')
    
    # Get email settings from database
    try:
        cur.execute('SELECT key, value FROM app_settings')
        settings = {row['key']: row['value'] for row in cur.fetchall()}
    except:
        settings = {}
    conn.close()
    
    # Get Brevo API key from environment variable
    brevo_api_key = os.environ.get('BREVO_API_KEY', '')
    sender_email = settings.get('sender_email', '')
    sender_name = settings.get('sender_name', 'Clean Ice West Scotland')
    
    if not brevo_api_key or not sender_email:
        return redirect(f'/bookings/{id}/certificate/{ba_id}?email=not_configured')
    
    try:
        # Create email body
        body = f'''
Dear {booking["contact_name"]},

Please find attached your cleaning certificate for the site visit on {booking["visit_date"].strftime("%d/%m/%Y")}.

If you have any questions, please don't hesitate to contact us.

Kind regards,
{sender_name}
        '''
        
        # Send email via Brevo API
        response = requests.post(
            'https://api.brevo.com/v3/smtp/email',
            headers={
                'accept': 'application/json',
                'api-key': brevo_api_key,
                'content-type': 'application/json'
            },
            json={
                'sender': {'name': sender_name, 'email': sender_email},
                'to': [{'email': booking['email'], 'name': booking['contact_name']}],
                'subject': f'Cleaning Certificate - {booking["business_name"]}',
                'textContent': body
            },
            timeout=10
        )
        
        if response.status_code in [200, 201]:
            return redirect(f'/bookings/{id}/certificate/{ba_id}?email=sent')
        else:
            print(f"BREVO ERROR: {response.status_code} - {response.text}")
            return redirect(f'/bookings/{id}/certificate/{ba_id}?email=failed')
    except Exception as e:
        import traceback
        print(f"EMAIL ERROR: {traceback.format_exc()}")
        return redirect(f'/bookings/{id}/certificate/{ba_id}?email=failed')
@app.route('/bookings/<int:id>/certificate/<int:ba_id>/pdf')
def certificate_pdf(id, ba_id):
    """Generate PDF certificate"""
    from flask import make_response
    import io
    
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    
    cur.execute('''
        SELECT b.*, c.business_name, c.contact_name, c.address, c.postcode, c.email,
               e.name as engineer_name
        FROM bookings b
        JOIN customers c ON b.customer_id = c.id
        LEFT JOIN engineers e ON b.engineer_id = e.id
        WHERE b.id=%s
    ''', (id,))
    booking = cur.fetchone()
    
    cur.execute('''
        SELECT ba.*, a.machine_make, a.machine_model, a.serial_number, a.location_notes
        FROM booking_assets ba
        JOIN assets a ON ba.asset_id = a.id
        WHERE ba.id=%s
    ''', (ba_id,))
    asset = cur.fetchone()
    
    cur.execute('SELECT * FROM atp_readings WHERE booking_asset_id=%s', (ba_id,))
    readings = cur.fetchall()
    
    conn.close()
    
    if not booking or not asset:
        return redirect('/bookings')
    
    # For now, redirect to the certificate page (PDF generation needs weasyprint)
    # When weasyprint is installed, this will generate a proper PDF
    return redirect(f'/bookings/{id}/certificate/{ba_id}')

@app.route('/atp')
def atp_readings():
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute('''
        SELECT ar.*, b.visit_date, c.business_name, a.machine_make, a.machine_model
        FROM atp_readings ar
        JOIN booking_assets ba ON ar.booking_asset_id = ba.id
        JOIN bookings b ON ba.booking_id = b.id
        JOIN customers c ON b.customer_id = c.id
        JOIN assets a ON ba.asset_id = a.id
        ORDER BY b.visit_date DESC
    ''')
    readings = cur.fetchall()
    conn.close()
    return render_template('atp.html', readings=readings)

@app.route('/passport')
def passport():
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute('''
        SELECT a.*, c.business_name,
            (SELECT MAX(b.visit_date) FROM booking_assets ba 
             JOIN bookings b ON ba.booking_id = b.id 
             WHERE ba.asset_id = a.id) as last_visit
        FROM assets a
        JOIN customers c ON a.customer_id = c.id
        ORDER BY c.business_name
    ''')
    assets = cur.fetchall()
    conn.close()
    return render_template('passport.html', assets=assets)

@app.route('/passport/<int:id>')
def passport_detail(id):
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    
    cur.execute('''
        SELECT a.*, c.business_name, c.contact_name, c.phone, c.email, c.address, c.postcode
        FROM assets a
        JOIN customers c ON a.customer_id = c.id
        WHERE a.id=%s
    ''', (id,))
    asset = cur.fetchone()
    
    cur.execute('''
        SELECT ba.*, b.visit_date, e.name as engineer_name
        FROM booking_assets ba
        JOIN bookings b ON ba.booking_id = b.id
        LEFT JOIN engineers e ON b.engineer_id = e.id
        WHERE ba.asset_id=%s
        ORDER BY b.visit_date DESC
    ''', (id,))
    service_history = cur.fetchall()
    
    history_data = []
    for record in service_history:
        cur.execute('SELECT * FROM atp_readings WHERE booking_asset_id=%s', (record['id'],))
        readings = cur.fetchall()
        history_data.append({'record': record, 'readings': readings})
    
    conn.close()
    return render_template('passport_detail.html', asset=asset, history_data=history_data)

@app.route('/calendar')
def calendar_view():
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    
    # Get current month/year from query params or default to today
    from datetime import date
    today = date.today()
    year = request.args.get('year', today.year, type=int)
    month = request.args.get('month', today.month, type=int)
    
    # Get all bookings for this month
    cur.execute('''
        SELECT b.*, c.business_name, e.name as engineer_name
        FROM bookings b
        JOIN customers c ON b.customer_id = c.id
        LEFT JOIN engineers e ON b.engineer_id = e.id
        WHERE EXTRACT(MONTH FROM b.visit_date) = %s 
        AND EXTRACT(YEAR FROM b.visit_date) = %s
        ORDER BY b.visit_date
    ''', (month, year))
    bookings = cur.fetchall()
    conn.close()
    
    # Build calendar grid
    import calendar as cal
    cal.setfirstweekday(0)  # Monday first
    month_calendar = cal.monthcalendar(year, month)
    month_name = cal.month_name[month]
    
    return render_template('calendar.html', 
                          bookings=bookings,
                          month_calendar=month_calendar,
                          month_name=month_name,
                          year=year,
                          month=month,
                          today=today)

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    
    # Create settings table if it doesn't exist
    cur.execute('''CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    conn.commit()
    
    if request.method == 'POST':
        # Save settings to database
        settings_to_save = {
            'company_name': request.form.get('company_name', ''),
            'smtp_server': request.form.get('smtp_server', 'smtp.gmail.com'),
            'smtp_port': request.form.get('smtp_port', '587'),
            'sender_email': request.form.get('sender_email', ''),
            'sender_name': request.form.get('sender_name', '')
        }
        for key, value in settings_to_save.items():
            cur.execute('''INSERT INTO app_settings (key, value) VALUES (%s, %s)
                         ON CONFLICT (key) DO UPDATE SET value = %s''', (key, value, value))
        conn.commit()
        conn.close()
        return render_template('settings.html', saved=True, **settings_to_save)
    
    # Load settings from database
    cur.execute('SELECT key, value FROM app_settings')
    settings_dict = {row['key']: row['value'] for row in cur.fetchall()}
    conn.close()
    
    return render_template('settings.html', saved=False,
                         company_name=settings_dict.get('company_name', ''),
                         smtp_server=settings_dict.get('smtp_server', 'smtp.gmail.com'),
                         smtp_port=settings_dict.get('smtp_port', '587'),
                         sender_email=settings_dict.get('sender_email', ''),
                         sender_name=settings_dict.get('sender_name', ''))

@app.route('/checkin/<int:booking_id>')
def checkin(booking_id):
    from datetime import datetime
    conn = get_db()
    cur = conn.cursor()
    cur.execute('UPDATE bookings SET check_in_time=%s, status=%s WHERE id=%s', 
                (datetime.now(), 'in_progress', booking_id))
    conn.commit()
    conn.close()
    return redirect(f'/bookings/{booking_id}')

@app.route('/checkout/<int:booking_id>')
def checkout(booking_id):
    from datetime import datetime
    conn = get_db()
    cur = conn.cursor()
    cur.execute('UPDATE bookings SET check_out_time=%s, status=%s WHERE id=%s',
                (datetime.now(), 'completed', booking_id))
    conn.commit()
    conn.close()
    return redirect(f'/bookings/{booking_id}')

@app.route('/qr/<int:booking_id>')
def qr_code(booking_id):
    import qrcode
    import io
    from flask import send_file
    
    # Generate QR code with check-in URL
    checkin_url = f"https://clean-ice-app-6.onrender.com/checkin/{booking_id}"
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(checkin_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1a5276", back_color="white")
    
    # Save to bytes
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    
    return send_file(img_io, mimetype='image/png', as_attachment=True, download_name=f'booking_{booking_id}_qr.png')
def settings():
    if request.method == 'POST':
        EMAIL_CONFIG['smtp_server'] = request.form.get('smtp_server', '')
        EMAIL_CONFIG['smtp_port'] = int(request.form.get('smtp_port', 587))
        EMAIL_CONFIG['sender_email'] = request.form.get('sender_email', '')
        EMAIL_CONFIG['sender_password'] = request.form.get('sender_password', '')
        return redirect('/settings?saved=1')
    return render_template('settings.html', config=EMAIL_CONFIG)

@app.route('/import')
def import_page():
    return render_template('import.html')

@app.route('/download-template/<filename>')
def download_template(filename):
    import os
    template_dir = os.path.join(os.path.dirname(__file__), 'templates')
    file_path = os.path.join(template_dir, filename)
    if os.path.exists(file_path) and filename.endswith('.csv'):
        from flask import send_file
        return send_file(file_path, as_attachment=True)
    return redirect('/import')

@app.route('/import/customers', methods=['POST'])
def import_customers():
    if 'file' not in request.files:
        return redirect('/import')
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        return redirect('/import')
    
    stream = file.stream.read().decode('utf-8-sig')
    reader = csv.DictReader(stream.splitlines())
    conn = get_db()
    cur = conn.cursor()
    imported = 0
    for row in reader:
        try:
            # Accept both formats: "Business Name" and "business_name"
            biz = row.get('business_name') or row.get('Business Name') or row.get('Business') or ''
            contact = row.get('contact_name') or row.get('Contact Name') or row.get('Contact') or ''
            phone = row.get('phone') or row.get('Phone') or ''
            email = row.get('email') or row.get('Email') or ''
            address = row.get('address') or row.get('Address') or ''
            postcode = row.get('postcode') or row.get('Postcode') or ''
            
            cur.execute('''INSERT INTO customers 
                (business_name, contact_name, phone, email, address, postcode) 
                VALUES (%s, %s, %s, %s, %s, %s)''',
                (biz, contact, phone, email, address, postcode))
            imported += 1
        except:
            continue
    conn.commit()
    conn.close()
    return render_template('import_result.html', imported=imported, table='customers')

@app.route('/import/assets', methods=['POST'])
def import_assets():
    if 'file' not in request.files:
        return redirect('/import')
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        return redirect('/import')
    
    stream = file.stream.read().decode('utf-8-sig')
    reader = csv.DictReader(stream.splitlines())
    conn = get_db()
    cur = conn.cursor()
    imported = 0
    for row in reader:
        try:
            cur.execute('SELECT id FROM customers WHERE business_name=%s', (row.get('customer_name', ''),))
            customer = cur.fetchone()
            if not customer:
                continue
            cur.execute('''INSERT INTO assets 
                (customer_id, machine_make, machine_model, serial_number, location_notes, install_date, cleaning_interval_months) 
                VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                (customer[0], row.get('machine_make', ''), row.get('machine_model', ''),
                 row.get('serial_number', ''), row.get('location_notes', ''),
                 row.get('install_date', ''), row.get('cleaning_interval_months', 6)))
            imported += 1
        except:
            continue
    conn.commit()
    conn.close()
    return render_template('import_result.html', imported=imported, table='assets')

@app.route('/import/suretrend', methods=['POST'])
def import_suretrend():
    if 'file' not in request.files:
        return redirect('/import')
    file = request.files['file']
    if file.filename == '' or not (file.filename.endswith('.csv') or file.filename.endswith('.CSV')):
        return redirect('/import')
    
    stream = file.stream.read().decode('utf-8-sig')
    reader = csv.DictReader(stream.splitlines())
    conn = get_db()
    cur = conn.cursor()
    imported = 0
    skipped = 0
    
    for row in reader:
        try:
            date_val = row.get('Date') or row.get('Test Date') or row.get('DATE') or ''
            time_val = row.get('Time') or row.get('Test Time') or row.get('TIME') or ''
            location = row.get('Location') or row.get('Location Name') or row.get('LOCATION') or ''
            rlu_val = row.get('RLU') or row.get('Result') or row.get('Reading') or row.get('RLU Value') or ''
            pass_fail = row.get('Pass/Fail') or row.get('Status') or row.get('PASS/FAIL') or ''
            notes = row.get('Notes') or row.get('Comment') or ''
            
            if not date_val or not rlu_val:
                skipped += 1
                continue
            
            visit_date = f"{date_val.strip()} {time_val.strip()}" if time_val else date_val.strip()
            
            try:
                rlu_numeric = float(str(rlu_val).replace(',', ''))
            except:
                skipped += 1
                continue
            
            if pass_fail:
                pf = 'pass' if 'pass' in pass_fail.lower() else 'fail' if 'fail' in pass_fail.lower() else ('pass' if rlu_numeric < 100 else 'fail')
            else:
                pf = 'pass' if rlu_numeric < 100 else 'fail'
            
            cur.execute('SELECT id, customer_id FROM assets WHERE status=%s LIMIT 1', ('active',))
            asset = cur.fetchone()
            if not asset:
                skipped += 1
                continue
            
            cur.execute('''INSERT INTO bookings 
                (customer_id, engineer_id, visit_date, notes, status) 
                VALUES (%s, 1, %s, %s, 'completed') RETURNING id''',
                (asset[1], visit_date, f"SureTrend: {location}" if location else 'Imported from SureTrend'))
            booking_id = cur.fetchone()[0]
            
            cur.execute('''INSERT INTO booking_assets 
                (booking_id, asset_id, work_completed, sanitisation_done, overall_condition, notes) 
                VALUES (%s, %s, 'ATP Test', 1, %s, %s) RETURNING id''',
                (booking_id, asset[0], 'pass' if pf == 'pass' else 'fair', f"SureTrend: {location}"))
            ba_id = cur.fetchone()[0]
            
            cur.execute('''INSERT INTO atp_readings 
                (booking_asset_id, reading_value, unit, pass_fail, location_tested, notes) 
                VALUES (%s, %s, 'RLU', %s, %s, %s)''',
                (ba_id, rlu_numeric, pf, location, notes))
            
            imported += 1
        except Exception as e:
            skipped += 1
            continue
    
    conn.commit()
    conn.close()
    return render_template('import_result.html', imported=imported, skipped=skipped, table='SureTrend ATP readings')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
