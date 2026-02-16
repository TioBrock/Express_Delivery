import os
import logging
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import json
from sqlalchemy import extract
from collections import defaultdict

# Configurar logs para debug
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'lasanha_2026_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///lasanha_v3.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Apenas uma instância de SQLAlchemy - adicione logs para verificar
try:
    db = SQLAlchemy(app)
    logger.info("SQLAlchemy instance created successfully.")
except RuntimeError as e:
    logger.error(f"SQLAlchemy error: {e}")
    raise

# --- MODELOS ---

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password_hash = db.Column(db.String(128))

class Configuracao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    preco_marmita = db.Column(db.Float, default=15.00)
    # Gás
    preco_botijao = db.Column(db.Float, default=115.00)
    horas_duracao_botijao = db.Column(db.Float, default=50.0)
    tempo_forno_minutos = db.Column(db.Float, default=45.0)
    marmitas_por_fornada = db.Column(db.Integer, default=10)
    # Entrega
    preco_gasolina = db.Column(db.Float, default=6.00)
    km_por_litro = db.Column(db.Float, default=35.0)
    km_media_entrega = db.Column(db.Float, default=5.0)

class Receita(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True)

class ReceitaItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    receita_id = db.Column(db.Integer, db.ForeignKey('receita.id'))
    ingrediente_id = db.Column(db.Integer, db.ForeignKey('ingrediente.id'))
    qtd_usada_por_fornada = db.Column(db.Float)
    unidade_uso = db.Column(db.String(10))
    receita = db.relationship('Receita', backref=db.backref('itens', cascade="all,delete"))
    ingrediente = db.relationship('Ingrediente', backref=db.backref('usos', cascade="all,delete"))

class Combo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True)

class ComboReceita(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    combo_id = db.Column(db.Integer, db.ForeignKey('combo.id'))
    receita_id = db.Column(db.Integer, db.ForeignKey('receita.id'))
    combo = db.relationship('Combo', backref=db.backref('receitas', cascade="all,delete"))
    receita = db.relationship('Receita', backref=db.backref('combos', cascade="all,delete"))

class Ingrediente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100))
    unidade_compra = db.Column(db.String(10))
    preco_pago = db.Column(db.Float)
    qtd_embalagem = db.Column(db.Float)
    data_compra = db.Column(db.DateTime, default=datetime.now)

class Venda(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.DateTime, default=datetime.now)
    quantidade = db.Column(db.Integer)
    valor_total = db.Column(db.Float)
    custo_total = db.Column(db.Float)
    lucro_total = db.Column(db.Float)

# --- LÓGICA DE AUXÍLIO ---

def obter_config():
    conf = Configuracao.query.first()
    if not conf:
        conf = Configuracao()
        db.session.add(conf)
        db.session.commit()
    return conf

def custo_unitario_ingrediente(ingrediente_id):
    ing = Ingrediente.query.get(ingrediente_id)
    if not ing:
        return 0
    base = ing.qtd_embalagem * 1000 if ing.unidade_compra in ['Kg', 'L'] else ing.qtd_embalagem
    return ing.preco_pago / base if base > 0 else 0

def calcular_custo_unidade(combo=None):
    conf = obter_config()
    if not combo:
        combo = Combo.query.filter(Combo.nome.ilike('Lasanha Completa')).first()
        if not combo:
            combo = Combo.query.first()
    if not combo:
        return 0
    
    custo_ingredientes_por_fornada = 0
    for combo_rec in combo.receitas:
        receita = combo_rec.receita
        for item in receita.itens:
            custo_unit = custo_unitario_ingrediente(item.ingrediente_id)
            custo_ingredientes_por_fornada += custo_unit * item.qtd_usada_por_fornada
    
    custo_gas_por_fornada = (conf.preco_botijao / (conf.horas_duracao_botijao * 60)) * conf.tempo_forno_minutos
    custo_entrega_por_fornada = (conf.km_media_entrega / conf.km_por_litro) * conf.preco_gasolina
    
    custo_total_por_fornada = custo_ingredientes_por_fornada + custo_gas_por_fornada + custo_entrega_por_fornada
    return custo_total_por_fornada / conf.marmitas_por_fornada if conf.marmitas_por_fornada > 0 else 0

def calcular_custo_por_item(combo=None):
    if not combo:
        combo = Combo.query.filter(Combo.nome.ilike('Lasanha Completa')).first()
        if not combo:
            combo = Combo.query.first()
    if not combo:
        return [], []
    labels = []
    gastos = []
    for combo_rec in combo.receitas:
        receita = combo_rec.receita
        for item in receita.itens:
            custo_unit = custo_unitario_ingrediente(item.ingrediente_id)
            custo = custo_unit * item.qtd_usada_por_fornada
            labels.append(f"{receita.nome}: {item.ingrediente.nome}")
            gastos.append(round(custo, 2))
    return labels, gastos

# --- ROTAS ---

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conf = obter_config()
    filtro = request.args.get('periodo', '7')
    combo_id = request.args.get('combo')
    hoje = datetime.now()

    query = Venda.query
    if filtro == '7':
        query = query.filter(Venda.data >= hoje - timedelta(days=7))
    elif filtro == '30':
        query = query.filter(Venda.data >= hoje - timedelta(days=30))
    elif filtro == 'completo':
        pass
    elif filtro.startswith('mes-'):
        mes = int(filtro.split('-')[1])
        query = query.filter(extract('month', Venda.data) == mes, extract('year', Venda.data) == hoje.year)
    
    vendas = query.all()
    bruto = sum(v.valor_total for v in vendas)
    custos = sum(v.custo_total for v in vendas)
    lucro = sum(v.lucro_total for v in vendas)

    combos = Combo.query.all()
    combo_ativo = None
    if combo_id:
        combo_ativo = Combo.query.get(int(combo_id))
    if not combo_ativo and combos:
        combo_ativo = combos[0]

    labels_itens, gastos_itens = calcular_custo_por_item(combo_ativo)

    dados_diarios = defaultdict(lambda: {'lucro': 0, 'custo': 0})
    for v in vendas:
        dia = v.data.strftime('%d/%m')
        dados_diarios[dia]['lucro'] += v.lucro_total
        dados_diarios[dia]['custo'] += v.custo_total
    
    graph_labels = list(dados_diarios.keys())
    graph_lucro = [dados_diarios[d]['lucro'] for d in graph_labels]
    graph_custo = [dados_diarios[d]['custo'] for d in graph_labels]

    return render_template('dashboard.html', 
                           bruto=bruto, gastos=custos, lucro=lucro, 
                           custo_un=calcular_custo_unidade(combo_ativo),
                           preco_venda=conf.preco_marmita,
                           periodo=filtro, conf=conf,
                           labels_itens=json.dumps(labels_itens),
                           gastos_itens=json.dumps(gastos_itens),
                           graph_labels=json.dumps(graph_labels),
                           graph_lucro=json.dumps(graph_lucro),
                           graph_custo=json.dumps(graph_custo),
                           combo_ativo=combo_ativo, combos=combos,
                           custo_unitario_ingrediente=custo_unitario_ingrediente)

@app.route('/vender', methods=['POST'])
def vender():
    conf = obter_config()
    qtd = int(request.form.get('quantidade', 1))
    combo_id = request.args.get('combo')
    combo_ativo = Combo.query.get(int(combo_id)) if combo_id else None
    custo_un = calcular_custo_unidade(combo_ativo)
    
    nova_venda = Venda(
        quantidade=qtd,
        valor_total=qtd * conf.preco_marmita,
        custo_total=qtd * custo_un,
        lucro_total=qtd * (conf.preco_marmita - custo_un)
    )
    db.session.add(nova_venda)
    db.session.commit()
    return redirect(url_for('dashboard') + (f'?combo={combo_id}&periodo={request.args.get("periodo", "7")}' if combo_id else ''))

@app.route('/config')
def config():
    if 'user_id' not in session: return redirect(url_for('login'))
    conf = obter_config()
    return render_template('config.html', conf=conf)

@app.route('/config/salvar', methods=['POST'])
def salvar_config():
    conf = obter_config()
    conf.preco_marmita = float(request.form['preco_marmita'])
    conf.preco_botijao = float(request.form['preco_botijao'])
    conf.horas_duracao_botijao = float(request.form['horas_duracao_botijao'])
    conf.tempo_forno_minutos = float(request.form['tempo_forno_minutos'])
    conf.marmitas_por_fornada = int(request.form['marmitas_por_fornada'])
    conf.preco_gasolina = float(request.form['preco_gasolina'])
    conf.km_por_litro = float(request.form['km_por_litro'])
    conf.km_media_entrega = float(request.form['km_media_entrega'])
    db.session.commit()
    flash('Configurações salvas!')
    return redirect(url_for('config'))

@app.route('/config/preco', methods=['POST'])
def salvar_preco():
    conf = obter_config()
    conf.preco_marmita = float(request.form['preco'])
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/receitas')
def receitas():
    if 'user_id' not in session: return redirect(url_for('login'))
    receitas = Receita.query.all()
    return render_template('receitas.html', receitas=receitas)

@app.route('/receita/salvar', methods=['POST'])
def salvar_receita():
    rec_id = request.form.get('id')
    if rec_id:
        rec = Receita.query.get(rec_id)
    else:
        rec = Receita()
        db.session.add(rec)
    
    rec.nome = request.form['nome']
    db.session.commit()
    return redirect(url_for('receitas'))

@app.route('/receita/deletar/<int:id>')
def deletar_receita(id):
    if 'user_id' not in session: return redirect(url_for('login'))
    Receita.query.filter_by(id=id).delete()
    db.session.commit()
    return redirect(url_for('receitas'))

@app.route('/receita/<int:id>/itens')
def itens_receita(id):
    if 'user_id' not in session: return redirect(url_for('login'))
    receita = Receita.query.get(id)
    ingredientes = Ingrediente.query.all()
    return render_template('itens_receita.html', receita=receita, ingredientes=ingredientes, custo_unitario_ingrediente=custo_unitario_ingrediente)

@app.route('/receita/item/salvar', methods=['POST'])
def salvar_item_receita():
    item_id = request.form.get('id')
    if item_id:
        item = ReceitaItem.query.get(item_id)
    else:
        item = ReceitaItem()
        db.session.add(item)
    
    item.receita_id = request.form['receita_id']
    item.ingrediente_id = request.form['ingrediente_id']
    item.qtd_usada_por_fornada = float(request.form['qtd_uso'])
    item.unidade_uso = request.form['uni_uso']
    db.session.commit()
    return redirect(url_for('itens_receita', id=item.receita_id))

@app.route('/combos')
def combos():
    if 'user_id' not in session: return redirect(url_for('login'))
    combos = Combo.query.all()
    return render_template('combos.html', combos=combos)

@app.route('/combo/salvar', methods=['POST'])
def salvar_combo():
    combo_id = request.form.get('id')
    if combo_id:
        combo = Combo.query.get(combo_id)
    else:
        combo = Combo()
        db.session.add(combo)
    
    combo.nome = request.form['nome']
    db.session.commit()
    return redirect(url_for('combos'))

@app.route('/combo/<int:id>/receitas')
def receitas_combo(id):
    if 'user_id' not in session: return redirect(url_for('login'))
    combo = Combo.query.get(id)
    receitas = Receita.query.all()
    return render_template('receitas_combo.html', combo=combo, receitas=receitas)

@app.route('/combo/receita/salvar', methods=['POST'])
def salvar_receita_combo():
    combo_id = request.form['combo_id']
    receita_id = request.form['receita_id']
    if not ComboReceita.query.filter_by(combo_id=combo_id, receita_id=receita_id).first():
        combo_rec = ComboReceita(combo_id=combo_id, receita_id=receita_id)
        db.session.add(combo_rec)
        db.session.commit()
    return redirect(url_for('receitas_combo', id=combo_id))

@app.route('/combo/receita/remover/<int:id>')
def remover_receita_combo(id):
    if 'user_id' not in session: return redirect(url_for('login'))
    ComboReceita.query.filter_by(id=id).delete()
    db.session.commit()
    return redirect(request.referrer or url_for('combos'))

@app.route('/gastos')
def gastos():
    if 'user_id' not in session: return redirect(url_for('login'))
    ingredientes = Ingrediente.query.all()
    return render_template('gastos.html', ingredientes=ingredientes)

@app.route('/gasto/salvar', methods=['POST'])
def salvar_gasto():
    ing_id = request.form.get('id')
    if ing_id:
        ing = Ingrediente.query.get(ing_id)
    else:
        ing = Ingrediente()
        db.session.add(ing)
    
    ing.nome = request.form['nome']
    ing.preco_pago = float(request.form['preco_pago'])
    ing.qtd_embalagem = float(request.form['qtd_emb'])
    ing.unidade_compra = request.form['uni_emb']
    db.session.commit()
    return redirect(url_for('gastos'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if not Usuario.query.filter_by(username='elayne').first():
        db.session.add(Usuario(username='elayne', password_hash=generate_password_hash('123elane321')))
        db.session.commit()
    if request.method == 'POST':
        user = Usuario.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)