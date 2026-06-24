import os
import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request, send_from_directory, render_template, make_response, session, redirect, url_for
from dotenv import load_dotenv
from flask_cors import CORS
import datetime
import traceback
import decimal
import bcrypt

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='', template_folder='templates')
app.secret_key = os.getenv('SECRET_KEY', 'nearestmetro-secret-key-2025')
CORS(app)

# ── DB Connection ──────────────────────────────────────────────
def get_db_connection():
    conn = psycopg2.connect(os.getenv('DATABASE_URL'))
    return conn

# ── Data formatter (dates, decimals) ─────────────────────────
def format_db_data(data_dict):
    if not isinstance(data_dict, dict):
        return data_dict
    formatted = {}
    for key, value in data_dict.items():
        if isinstance(value, datetime.date):
            formatted[key] = value.strftime('%Y-%m-%d') if value else None
        elif isinstance(value, decimal.Decimal):
            try:
                formatted[key] = float(value)
            except (TypeError, ValueError):
                formatted[key] = None
        else:
            formatted[key] = value
    return formatted

# ── Auth helper ───────────────────────────────────────────────
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_id'):
            return redirect('/admin/login')
        return f(*args, **kwargs)
    return decorated


# ════════════════════════════════════════════════════════════
#  HTML PAGE ROUTES
# ════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/stations/<slug>')
def station_detail(slug):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT s.*,
                   l.name as line_name, l.slug as line_slug, l.color as line_color,
                   ci.name as city_name, ci.slug as city_slug,
                   co.name as country_name, co.slug as country_slug, co.code as country_code
            FROM stations s
            LEFT JOIN lines l ON s.line_id = l.id
            LEFT JOIN cities ci ON s.city_id = ci.id
            LEFT JOIN countries co ON s.country_id = co.id
            WHERE s.slug = %s AND s.active = TRUE
        """, (slug,))
        station = cur.fetchone()
        if not station:
            return "Station not found", 404
        cur.close()
        return render_template('station.html', station=format_db_data(dict(station)))
    except Exception as e:
        traceback.print_exc()
        return "Error loading station", 500
    finally:
        if conn: conn.close()

@app.route('/country/<country_slug>')
def country_page(country_slug):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM countries WHERE slug = %s AND active = TRUE", (country_slug,))
        country = cur.fetchone()
        if not country:
            return "Country not found", 404
        cur.execute("SELECT * FROM cities WHERE country_id = %s AND active = TRUE ORDER BY name", (country['id'],))
        cities = [dict(c) for c in cur.fetchall()]
        cur.close()
        return render_template('city.html', country=dict(country), cities=cities, city=None)
    except Exception as e:
        traceback.print_exc()
        return "Error loading country", 500
    finally:
        if conn: conn.close()

@app.route('/country/<country_slug>/<city_slug>')
def city_page(country_slug, city_slug):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM countries WHERE slug = %s AND active = TRUE", (country_slug,))
        country = cur.fetchone()
        if not country:
            return "Country not found", 404
        cur.execute("SELECT * FROM cities WHERE slug = %s AND country_id = %s AND active = TRUE",
                    (city_slug, country['id']))
        city = cur.fetchone()
        if not city:
            return "City not found", 404
        cur.execute("SELECT * FROM lines WHERE city_id = %s AND active = TRUE ORDER BY name", (city['id'],))
        lines = [dict(l) for l in cur.fetchall()]
        cur.close()
        return render_template('city.html', country=dict(country), city=dict(city), lines=lines, cities=None)
    except Exception as e:
        traceback.print_exc()
        return "Error loading city", 500
    finally:
        if conn: conn.close()

@app.route('/blog')
def blog():
    return render_template('blog.html')

@app.route('/blog/<slug>')
def blog_post(slug):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM posts WHERE slug = %s AND active = TRUE", (slug,))
        post = cur.fetchone()
        cur.close()
        if not post:
            return "Post not found", 404
        return render_template('post.html', post=format_db_data(dict(post)))
    except Exception as e:
        traceback.print_exc()
        return "Error loading post", 500
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════
#  API — STATIONS
# ════════════════════════════════════════════════════════════

@app.route('/api/stations')
def api_stations():
    conn = None
    try:
        lat         = request.args.get('lat', type=float)
        lng         = request.args.get('lng', type=float)
        country_slug = request.args.get('country')
        city_slug    = request.args.get('city')
        line_slug    = request.args.get('line')

        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        filters = "WHERE s.active = TRUE"
        params  = []

        if country_slug:
            filters += " AND co.slug = %s"
            params.append(country_slug)
        if city_slug:
            filters += " AND ci.slug = %s"
            params.append(city_slug)
        if line_slug:
            filters += " AND l.slug = %s"
            params.append(line_slug)

        if lat and lng:
            filters += " AND s.lat IS NOT NULL"
            cur.execute(f"""
                SELECT s.*,
                       l.name as line_name, l.color as line_color,
                       ci.name as city_name, ci.slug as city_slug,
                       co.name as country_name, co.slug as country_slug,
                       (6371000 * acos(
                           cos(radians(%s)) * cos(radians(s.lat)) *
                           cos(radians(s.lng) - radians(%s)) +
                           sin(radians(%s)) * sin(radians(s.lat))
                       )) AS distance_m
                FROM stations s
                LEFT JOIN lines l ON s.line_id = l.id
                LEFT JOIN cities ci ON s.city_id = ci.id
                LEFT JOIN countries co ON s.country_id = co.id
                {filters}
                ORDER BY distance_m
            """, [lat, lng, lat] + params)
        else:
            cur.execute(f"""
                SELECT s.*,
                       l.name as line_name, l.color as line_color,
                       ci.name as city_name, ci.slug as city_slug,
                       co.name as country_name, co.slug as country_slug
                FROM stations s
                LEFT JOIN lines l ON s.line_id = l.id
                LEFT JOIN cities ci ON s.city_id = ci.id
                LEFT JOIN countries co ON s.country_id = co.id
                {filters}
                ORDER BY s.name
            """, params)

        rows = [format_db_data(dict(r)) for r in cur.fetchall()]
        cur.close()
        return jsonify(rows)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': 'Error fetching stations'}), 500
    finally:
        if conn: conn.close()


@app.route('/api/stations/nearest')
def api_stations_nearest():
    conn = None
    try:
        lat   = request.args.get('lat', type=float)
        lng   = request.args.get('lng', type=float)
        limit = request.args.get('limit', 5, type=int)

        if not lat or not lng:
            return jsonify({'error': 'lat and lng are required'}), 400

        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT s.*,
                   l.name as line_name, l.color as line_color,
                   ci.name as city_name, ci.slug as city_slug,
                   co.name as country_name, co.slug as country_slug,
                   (6371000 * acos(
                       cos(radians(%s)) * cos(radians(s.lat)) *
                       cos(radians(s.lng) - radians(%s)) +
                       sin(radians(%s)) * sin(radians(s.lat))
                   )) AS distance_m
            FROM stations s
            LEFT JOIN lines l ON s.line_id = l.id
            LEFT JOIN cities ci ON s.city_id = ci.id
            LEFT JOIN countries co ON s.country_id = co.id
            WHERE s.active = TRUE AND s.lat IS NOT NULL
            ORDER BY distance_m
            LIMIT %s
        """, (lat, lng, lat, limit))
        rows = [format_db_data(dict(r)) for r in cur.fetchall()]
        cur.close()
        return jsonify(rows)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': 'Error fetching nearest stations'}), 500
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════
#  API — COUNTRIES / CITIES / LINES
# ════════════════════════════════════════════════════════════

@app.route('/api/countries')
def api_countries():
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM countries WHERE active = TRUE ORDER BY name")
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return jsonify(rows)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': 'Error fetching countries'}), 500
    finally:
        if conn: conn.close()


@app.route('/api/cities')
def api_cities():
    conn = None
    try:
        country_slug = request.args.get('country')
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if country_slug:
            cur.execute("""
                SELECT ci.* FROM cities ci
                JOIN countries co ON ci.country_id = co.id
                WHERE co.slug = %s AND ci.active = TRUE ORDER BY ci.name
            """, (country_slug,))
        else:
            cur.execute("SELECT * FROM cities WHERE active = TRUE ORDER BY name")
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return jsonify(rows)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': 'Error fetching cities'}), 500
    finally:
        if conn: conn.close()


@app.route('/api/lines')
def api_lines():
    conn = None
    try:
        city_slug = request.args.get('city')
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if city_slug:
            cur.execute("""
                SELECT l.* FROM lines l
                JOIN cities ci ON l.city_id = ci.id
                WHERE ci.slug = %s AND l.active = TRUE ORDER BY l.name
            """, (city_slug,))
        else:
            cur.execute("SELECT * FROM lines WHERE active = TRUE ORDER BY name")
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return jsonify(rows)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': 'Error fetching lines'}), 500
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════
#  API — RATINGS
# ════════════════════════════════════════════════════════════

@app.route('/api/stations/<int:station_id>/rating', methods=['GET'])
def api_station_rating(station_id):
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT ROUND(AVG(rating)::numeric, 1) as average, COUNT(*) as total
            FROM station_ratings WHERE station_id = %s
        """, (station_id,))
        row = cur.fetchone()
        cur.close()
        return jsonify({
            'average': float(row['average']) if row['average'] else 0,
            'total': int(row['total'])
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': 'Error fetching rating'}), 500
    finally:
        if conn: conn.close()


@app.route('/api/stations/<int:station_id>/rate', methods=['POST'])
def api_station_rate(station_id):
    conn = None
    try:
        data   = request.get_json()
        rating = data.get('rating')
        if not rating or int(rating) < 1 or int(rating) > 5:
            return jsonify({'ok': False, 'error': 'Rating must be between 1 and 5'}), 400

        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip and ',' in ip:
            ip = ip.split(',')[0].strip()

        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO station_ratings (station_id, ip, rating)
            VALUES (%s, %s, %s)
            ON CONFLICT (station_id, ip) DO UPDATE SET rating = EXCLUDED.rating
        """, (station_id, ip, int(rating)))
        conn.commit()
        cur.close()
        return jsonify({'ok': True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Error saving rating'}), 500
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════
#  API — COMMENTS
# ════════════════════════════════════════════════════════════

@app.route('/api/stations/<int:station_id>/comments', methods=['GET'])
def api_comments(station_id):
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, name, text, created_at FROM comments
            WHERE station_id = %s AND approved = TRUE
            ORDER BY created_at DESC
        """, (station_id,))
        rows = [format_db_data(dict(r)) for r in cur.fetchall()]
        cur.close()
        return jsonify(rows)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': 'Error fetching comments'}), 500
    finally:
        if conn: conn.close()


@app.route('/api/stations/<int:station_id>/comments', methods=['POST'])
def api_comment_new(station_id):
    conn = None
    try:
        data  = request.get_json()
        name  = (data.get('name') or '').strip()
        text  = (data.get('text') or '').strip()
        email = (data.get('email') or '').strip()
        if not name or not text:
            return jsonify({'ok': False, 'error': 'Name and comment are required'}), 400
        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO comments (station_id, name, email, text, approved)
            VALUES (%s, %s, %s, %s, FALSE)
        """, (station_id, name, email, text))
        conn.commit()
        cur.close()
        return jsonify({'ok': True, 'msg': 'Comment submitted and awaiting moderation!'})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Error saving comment'}), 500
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════
#  API — BLOG
# ════════════════════════════════════════════════════════════

@app.route('/api/blog')
def api_blog():
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM posts WHERE active = TRUE ORDER BY created_at DESC")
        rows = [format_db_data(dict(r)) for r in cur.fetchall()]
        cur.close()
        return jsonify(rows)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': 'Error fetching posts'}), 500
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════
#  ADMIN — LOGIN / LOGOUT
# ════════════════════════════════════════════════════════════

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        data  = request.get_json()
        email = data.get('email', '').strip()
        password = data.get('password', '')
        conn = None
        try:
            conn = get_db_connection()
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cur.fetchone()
            cur.close()
            if user and bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8')):
                session['admin_id']   = user['id']
                session['admin_name'] = user['name']
                return jsonify({'ok': True})
            return jsonify({'ok': False, 'error': 'Invalid email or password'}), 401
        except Exception as e:
            traceback.print_exc()
            return jsonify({'error': 'Internal error'}), 500
        finally:
            if conn: conn.close()
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect('/admin/login')

@app.route('/admin')
@login_required
def admin_index():
    return render_template('admin/index.html', name=session.get('admin_name'))


# ════════════════════════════════════════════════════════════
#  API ADMIN — STATIONS
# ════════════════════════════════════════════════════════════

@app.route('/api/admin/stations', methods=['GET', 'POST'])
@login_required
def api_admin_stations():
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if request.method == 'GET':
            cur.execute("""
                SELECT s.*,
                       l.name as line_name, ci.name as city_name, co.name as country_name
                FROM stations s
                LEFT JOIN lines l ON s.line_id = l.id
                LEFT JOIN cities ci ON s.city_id = ci.id
                LEFT JOIN countries co ON s.country_id = co.id
                ORDER BY s.name
            """)
            rows = [format_db_data(dict(r)) for r in cur.fetchall()]
            cur.close()
            return jsonify(rows)
        data = request.get_json()
        cur.execute("""
            INSERT INTO stations (name, slug, line_id, city_id, country_id, address, description,
                                  lat, lng, photo_url, active, featured)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (
            data.get('name',''), data.get('slug',''),
            data.get('line_id') or None, data.get('city_id') or None,
            data.get('country_id') or None,
            data.get('address',''), data.get('description',''),
            data.get('lat') or None, data.get('lng') or None,
            data.get('photo_url',''),
            data.get('active', True), data.get('featured', False)
        ))
        new_id = cur.fetchone()['id']
        conn.commit()
        cur.close()
        return jsonify({'ok': True, 'id': new_id})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/api/admin/stations/<int:station_id>', methods=['PUT', 'DELETE'])
@login_required
def api_admin_station(station_id):
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        if request.method == 'DELETE':
            cur.execute("DELETE FROM stations WHERE id = %s", (station_id,))
            conn.commit()
            cur.close()
            return jsonify({'ok': True})
        data = request.get_json()
        cur.execute("""
            UPDATE stations SET name=%s, slug=%s, line_id=%s, city_id=%s, country_id=%s,
            address=%s, description=%s, lat=%s, lng=%s, photo_url=%s, active=%s, featured=%s
            WHERE id=%s
        """, (
            data.get('name',''), data.get('slug',''),
            data.get('line_id') or None, data.get('city_id') or None,
            data.get('country_id') or None,
            data.get('address',''), data.get('description',''),
            data.get('lat') or None, data.get('lng') or None,
            data.get('photo_url',''),
            data.get('active', True), data.get('featured', False),
            station_id
        ))
        conn.commit()
        cur.close()
        return jsonify({'ok': True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════
#  API ADMIN — COMMENTS
# ════════════════════════════════════════════════════════════

@app.route('/api/admin/comments', methods=['GET'])
@login_required
def api_admin_comments():
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT c.*, s.name as station_name
            FROM comments c
            LEFT JOIN stations s ON c.station_id = s.id
            ORDER BY c.approved ASC, c.created_at DESC
        """)
        rows = [format_db_data(dict(r)) for r in cur.fetchall()]
        cur.close()
        return jsonify(rows)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/api/admin/comments/<int:com_id>/approve', methods=['POST'])
@login_required
def api_admin_approve_comment(com_id):
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute("UPDATE comments SET approved = TRUE WHERE id = %s", (com_id,))
        conn.commit()
        cur.close()
        return jsonify({'ok': True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/api/admin/comments/<int:com_id>', methods=['DELETE'])
@login_required
def api_admin_delete_comment(com_id):
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute("DELETE FROM comments WHERE id = %s", (com_id,))
        conn.commit()
        cur.close()
        return jsonify({'ok': True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════
#  API ADMIN — COUNTRIES
# ════════════════════════════════════════════════════════════

@app.route('/api/admin/countries', methods=['GET', 'POST'])
@login_required
def api_admin_countries():
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if request.method == 'GET':
            cur.execute("SELECT * FROM countries ORDER BY name")
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return jsonify(rows)
        data = request.get_json()
        cur.execute("""
            INSERT INTO countries (name, slug, code, active)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (data['name'], data['slug'], data['code'], data.get('active', True)))
        new_id = cur.fetchone()['id']
        conn.commit()
        cur.close()
        return jsonify({'ok': True, 'id': new_id})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/api/admin/countries/<int:country_id>', methods=['PUT', 'DELETE'])
@login_required
def api_admin_country(country_id):
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        if request.method == 'DELETE':
            cur.execute("DELETE FROM countries WHERE id = %s", (country_id,))
            conn.commit()
            cur.close()
            return jsonify({'ok': True})
        data = request.get_json()
        cur.execute("""
            UPDATE countries SET name=%s, slug=%s, code=%s, active=%s WHERE id=%s
        """, (data['name'], data['slug'], data['code'], data.get('active', True), country_id))
        conn.commit()
        cur.close()
        return jsonify({'ok': True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════
#  API ADMIN — CITIES
# ════════════════════════════════════════════════════════════

@app.route('/api/admin/cities', methods=['GET', 'POST'])
@login_required
def api_admin_cities():
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if request.method == 'GET':
            cur.execute("""
                SELECT ci.*, co.name as country_name
                FROM cities ci LEFT JOIN countries co ON ci.country_id = co.id
                ORDER BY ci.name
            """)
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return jsonify(rows)
        data = request.get_json()
        cur.execute("""
            INSERT INTO cities (name, slug, country_id, active)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (data['name'], data['slug'], data.get('country_id') or None, data.get('active', True)))
        new_id = cur.fetchone()['id']
        conn.commit()
        cur.close()
        return jsonify({'ok': True, 'id': new_id})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/api/admin/cities/<int:city_id>', methods=['PUT', 'DELETE'])
@login_required
def api_admin_city(city_id):
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        if request.method == 'DELETE':
            cur.execute("DELETE FROM cities WHERE id = %s", (city_id,))
            conn.commit()
            cur.close()
            return jsonify({'ok': True})
        data = request.get_json()
        cur.execute("""
            UPDATE cities SET name=%s, slug=%s, country_id=%s, active=%s WHERE id=%s
        """, (data['name'], data['slug'], data.get('country_id') or None, data.get('active', True), city_id))
        conn.commit()
        cur.close()
        return jsonify({'ok': True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════
#  API ADMIN — LINES
# ════════════════════════════════════════════════════════════

@app.route('/api/admin/lines', methods=['GET', 'POST'])
@login_required
def api_admin_lines():
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if request.method == 'GET':
            cur.execute("""
                SELECT l.*, ci.name as city_name
                FROM lines l LEFT JOIN cities ci ON l.city_id = ci.id
                ORDER BY l.name
            """)
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return jsonify(rows)
        data = request.get_json()
        cur.execute("""
            INSERT INTO lines (name, slug, color, city_id, active)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """, (data['name'], data['slug'], data.get('color',''), data.get('city_id') or None, data.get('active', True)))
        new_id = cur.fetchone()['id']
        conn.commit()
        cur.close()
        return jsonify({'ok': True, 'id': new_id})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/api/admin/lines/<int:line_id>', methods=['PUT', 'DELETE'])
@login_required
def api_admin_line(line_id):
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        if request.method == 'DELETE':
            cur.execute("DELETE FROM lines WHERE id = %s", (line_id,))
            conn.commit()
            cur.close()
            return jsonify({'ok': True})
        data = request.get_json()
        cur.execute("""
            UPDATE lines SET name=%s, slug=%s, color=%s, city_id=%s, active=%s WHERE id=%s
        """, (data['name'], data['slug'], data.get('color',''), data.get('city_id') or None, data.get('active', True), line_id))
        conn.commit()
        cur.close()
        return jsonify({'ok': True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════
#  API ADMIN — BLOG
# ════════════════════════════════════════════════════════════

@app.route('/api/admin/blog', methods=['GET', 'POST'])
@login_required
def api_admin_blog():
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if request.method == 'GET':
            cur.execute("SELECT * FROM posts ORDER BY created_at DESC")
            rows = [format_db_data(dict(r)) for r in cur.fetchall()]
            cur.close()
            return jsonify(rows)
        data = request.get_json()
        cur.execute("""
            INSERT INTO posts (title, slug, subtitle, author, content, image_url, active)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (
            data.get('title',''), data.get('slug',''),
            data.get('subtitle',''), data.get('author',''),
            data.get('content',''), data.get('image_url',''),
            data.get('active', True)
        ))
        new_id = cur.fetchone()['id']
        conn.commit()
        cur.close()
        return jsonify({'ok': True, 'id': new_id})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/api/admin/blog/<int:post_id>', methods=['PUT', 'DELETE'])
@login_required
def api_admin_post(post_id):
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        if request.method == 'DELETE':
            cur.execute("DELETE FROM posts WHERE id = %s", (post_id,))
            conn.commit()
            cur.close()
            return jsonify({'ok': True})
        data = request.get_json()
        cur.execute("""
            UPDATE posts SET title=%s, slug=%s, subtitle=%s, author=%s,
            content=%s, image_url=%s, active=%s WHERE id=%s
        """, (
            data.get('title',''), data.get('slug',''),
            data.get('subtitle',''), data.get('author',''),
            data.get('content',''), data.get('image_url',''),
            data.get('active', True), post_id
        ))
        conn.commit()
        cur.close()
        return jsonify({'ok': True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════
#  SITEMAP
# ════════════════════════════════════════════════════════════

@app.route('/sitemap.xml')
def sitemap():
    conn = None
    base = 'https://www.nearestmetro.com'
    urls = [base + '/', base + '/blog']
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute("SELECT slug FROM stations WHERE active = TRUE AND slug IS NOT NULL")
        for row in cur.fetchall():
            urls.append(f'{base}/stations/{row[0]}')
        cur.execute("""
            SELECT co.slug, ci.slug
            FROM cities ci JOIN countries co ON ci.country_id = co.id
            WHERE ci.active = TRUE
        """)
        for row in cur.fetchall():
            urls.append(f'{base}/country/{row[0]}/{row[1]}')
        cur.execute("SELECT slug FROM posts WHERE active = TRUE AND slug IS NOT NULL")
        for row in cur.fetchall():
            urls.append(f'{base}/blog/{row[0]}')
        cur.close()
    except Exception as e:
        print(f"WARNING: Error building sitemap: {e}")
    finally:
        if conn: conn.close()

    xml  = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for url in urls:
        xml += f'  <url><loc>{url}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>\n'
    xml += '</urlset>'
    return make_response(xml, 200, {'Content-Type': 'application/xml'})


# ════════════════════════════════════════════════════════════
#  STATIC FILES
# ════════════════════════════════════════════════════════════

@app.route('/<path:path>')
def serve_static(path):
    basename = os.path.basename(path)
    if '.' not in basename:
        return "Not Found", 404
    if os.path.exists(os.path.join('.', path)):
        return send_from_directory('.', path)
    return "Not Found", 404


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
