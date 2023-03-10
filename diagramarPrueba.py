#!/usr/bin/env python
# coding: utf-8

import os
import re
import time
import subprocess
import tempfile
import shutil
import asyncio
import warnings
import pandas as pd
import numpy as np
import streamlit as st
import jinja2
from zipfile import ZipFile
from bs4 import BeautifulSoup
from pyppeteer import launch
from numpy.random import default_rng
from PyPDF2 import PdfReader
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

async def get_browser():
  return await launch({
        'executablePath':'/usr/bin/google-chrome-stable',
        'headless':True,
        'args':['--no-sandbox','--disable-setuid-sandbox']
      },handleSIGINT=False,
      handleSIGTERM=False,
      handleSIGHUP=False)

async def html2pdf(file,sleep_time=0,page=None,browser=None,path=os.getcwd()):
  fname,extension = file.rsplit('.',1)
  if extension != 'html':
    raise ValueError(f"File {file} must be an html file")

  if not page:
    if not browser:
      browser = await get_browser()
    page = await browser.newPage()
  
  await page.goto(f"file://{path}/{fname}.html",waitUntil='networkidle2')
  time.sleep(sleep_time)
  await page.pdf({'path':f"{path}/{fname}.pdf",'printBackground':True, 'format':'A4'})

def merge_pdf(files,fname,path=os.getcwd()):
  outname = f"{path}/{fname}"
  total = ' '.join([f"\"{file}\"" for file in files])
  subprocess.call(f"pdftk {total} cat output \"{outname}\"",shell=True)
  return outname

def load_files(examen):
  l = []
  for i,sec in enumerate(examen['secciones']):
    d = pd.read_excel(sec['archivo']).reset_index().rename(columns={'index':'Pos'})
    d = d.set_index('Unique Id')
    d['Pos'] = d['Pos'] + 1
    d['Sec'] = sec['nombre']
    d['Ord'] = i + 1
    d['EsPadre'] = d['Total Points'] == 0.0
    d['Salto'] = False
    d['Continue'] = False
    d['Blanca'] = False
    d['Ultimo'] = False
    
    # Renumerar la prueba sin contar a los padres
    d = d.join(d.loc[d['EsPadre']==False,'Pos'].rank().rename('Ord_y'))
    d['Ord_y'] = d['Ord_y'].fillna(0).astype(int)
    d['Ord'] = d['Ord_y']
    d = d.drop(columns='Ord_y')
    
    for s in sec['saltos']:
      if s[-1] == '*':
        s = int(s[:-1])
        d.loc[d['Ord']==s,'Continue'] = True
      else:
        s = int(s)
      d.loc[d['Ord']==s,'Salto'] = True
    
    for b in sec['blancas']:
      d.loc[d['Ord']==b,'Blanca'] = True
    
    d.loc[d.iloc[-1:].index,'Ultimo'] = True

    l.append(d)

  df = pd.concat(l)

  #agregar numero de texto a los textos
  df = df.join(df.loc[df['EsPadre'],'Pos'].rank().astype(int).astype(str).rename('numtext'))

  # generar el orden de las alternativas
  rng = default_rng(examen['c??digo'])
  op = np.arange(1,5)
  df['orden'] = df.apply(lambda x: rng.permutation(op) if x['Alternativas en enunciado']!=True else op,axis=1)
  # calcular la nueva "clave"
  df['clave'] = df['orden'].apply(lambda x: np.nonzero(x ==1)[0][0] +1)
  #st.write(df)
  return df

def generate_anskey(examen,df,path=os.getcwd()):
  items = df[~df['EsPadre']]
  claves = items['clave'].apply(
    lambda x: chr(64+x)
  )
  claves = np.where(items['Alternativas en enunciado']==True,items['Answer 1'].str[3:-4],claves)
  name = f"{path}/CLAVE-{examen['versi??n']}-{examen['c??digo']}.xlsx"
  pd.DataFrame(claves,columns=[examen['versi??n']]).to_excel(
    name,
    index=False
  )
  return name

def generate_estructura(examen,df,path=os.getcwd()):
  items = df[~df['EsPadre']]
  comp = pd.read_excel('Temas.xlsx',sheet_name='Competencia',dtype=str).set_index('Bank')
  temas_com = pd.read_excel('Temas.xlsx',sheet_name='Comunicaci??n',dtype=str).set_index('Category Path')
  # Con el cambio a los nuevos temas esto no ser?? necesario
  if examen['versi??n'] == 'CIENCIAS':
    temas_mat = pd.read_excel('Temas.xlsx',sheet_name='Matem??tica Ciencias',dtype=str).set_index('Category Path')
  else:
    temas_mat = pd.read_excel('Temas.xlsx',sheet_name='Matem??tica Letras',dtype=str).set_index('Category Path')
  temas = pd.concat([temas_com,temas_mat])
  ruta = f"{path}/ESTRUCTURA-{examen['versi??n']}-{examen['c??digo']}.xlsx"
  est = items
  est = est.join(comp,on='Bank')
  est['Categor??a'] = '02'
  est['Error'] = ''
  est = est.join(temas,on='Category Path')
  est = est[['Item Name','Competencia','Tema','SubTema','Categor??a','Stat 3','IRT b','Error']]
  est['Posici??n'] = np.arange(est.shape[0])+1
  est.columns=['CodPregunta OCA','Competencia','Tema','SubTema','Categoria','N','Medici??n','Error','Posici??n\npregunta']
  est.to_excel(ruta,index=False)
  return ruta

def replace_equations(markup):
  if pd.isna(markup):
    return
  soup = BeautifulSoup(markup, 'html5lib')
  imgs = soup.find_all(class_='Wirisformula')
  for img in imgs:
    mathml = img['data-mathml'].replace('??','????').replace('??','????')
    img.replace_with(mathml)
  result = str(soup.body)[6:-7] # quitar <body></body> solo conservar lo de adentro
  result = result.replace('????','<').replace('????','>').replace('??','"').replace('??','&')
  result = result.replace('<math ','<math display="block"')
  matches = re.findall(r'<annotation encoding="LaTeX">.*?</annotation>',result,flags=re.DOTALL)
  for m in matches:
    result = result.replace(m,'')
  result = result.replace('<semantics>','').replace('</semantics>','')
  result = result.replace('\xa0',' ').replace(r'<p> </p>',' ')
  return result

def fix_images(markup):
  if pd.isna(markup):
    return
  soup = BeautifulSoup(markup, 'html5lib')
  imgs = soup.find_all('img')
  for img in imgs:
    img['src'] = 'https://app.fasttestweb.com' + img['src']
  return str(soup.body)[6:-7]

def process_items(df):
  # quitar algunos estilos
  df['Item Text'] = df['Item Text'].str.replace('style="font-family: \'times new roman\', times;"','')
  df['Item Text'] = df['Item Text'].str.replace('style="font-size: 12pt;"','')
  df['Item Text'] = df['Item Text'].str.replace('style="text-align: justify; font-size: 12pt;"','style="text-align: justify;"')

  # Reemplazar las acuaciones a MathML
  df['Item Text'] = df.apply(lambda x: replace_equations(x['Item Text']),axis=1)
  df['Answer 1'] = df.apply(lambda x: replace_equations(x['Answer 1']),axis=1)
  df['Answer 2'] = df.apply(lambda x: replace_equations(x['Answer 2']),axis=1)
  df['Answer 3'] = df.apply(lambda x: replace_equations(x['Answer 3']),axis=1)
  df['Answer 4'] = df.apply(lambda x: replace_equations(x['Answer 4']),axis=1)

  # Reemplazar las rutas de las imagenes
  df['Item Text'] = df.apply(lambda x: fix_images(x['Item Text']),axis=1)
  df['Answer 1'] = df.apply(lambda x: fix_images(x['Answer 1']),axis=1)
  df['Answer 2'] = df.apply(lambda x: fix_images(x['Answer 2']),axis=1)
  df['Answer 3'] = df.apply(lambda x: fix_images(x['Answer 3']),axis=1)
  df['Answer 4'] = df.apply(lambda x: fix_images(x['Answer 4']),axis=1)

  return df

def render_item(item_tpl,item,examen):
  return item_tpl.render(
    description = item['Item Text'],
    answer1 = item[f'Answer {item["orden"][0]}'], #segun el vector generado cual deber??a ser la primera alternativa
    answer2 = item[f'Answer {item["orden"][1]}'],
    answer3 = item[f'Answer {item["orden"][2]}'],
    answer4 = item[f'Answer {item["orden"][3]}'],
    padre = item['EsPadre'],
    num_texto = item['numtext'],
    salto = item['Salto'],
    cont = item['Continue'],
    blanca = item['Blanca'],
    resaltar_clave = examen['resaltar_clave'],
    clave = item['clave'],
    ocultar_alternativas = item['Alternativas en enunciado'],
  )

def generate_sec_html(df,sec,tpl,start=1,last=False,extra_css='',path=os.getcwd()):
  body = '\n'.join(df['html'])
  end = start + df[df['EsPadre']==False].shape[0] -1
  prueba = tpl.render(nombre=sec['nombre'],items=body,start=start,end = end, tiempo = sec['tiempo'],last=last,extra_css=extra_css)
  with open(f"{path}/{sec['nombre']}.html",'w') as f:
      f.write(prueba)

async def generate_content(examen,df,tpl,page=None,browser=None,path=os.getcwd()):
  start = 1
  if not page:
    if not browser:
      browser = await get_browser()
    page = await browser.newPage()

  for i,sec in enumerate(examen['secciones']):
    d = df.loc[df['Sec']==sec['nombre'],:]
    last = (i == (len(examen['secciones'])-1))
    generate_sec_html(d,sec,tpl,start,last,examen['extra_css'],path)
    start = start + d[d['EsPadre']==False].shape[0]
    await html2pdf(f"{sec['nombre']}.html",sleep_time=2,page=page,path=path)

def generate_background_html(sec,tpl,sec_num=1,start_page=2,path=os.getcwd()):
  reader = PdfReader(f"{path}/{sec['nombre']}.pdf")
  num_pages = len(reader.pages)
  numchars = len(sec['nombre'])
  if numchars < 22:
    namesize = 2.5
  else:
    namesize = 2.5 *(1-((numchars-21)/(numchars-1)))
  html = tpl.render(num_pages=num_pages,start_page=start_page,sec_num=sec_num,sec_name=sec['nombre'],size=namesize)
  with open(f'{path}/{sec["nombre"]}-background.html','w') as f:
    f.write(html)
  return num_pages

async def generate_backgrounds(examen,tpl,start_page=2,page=None,browser=None,path=os.getcwd()):
  if not page:
    if not browser:
      browser = await get_browser()
    page = await browser.newPage()
  
  for i,sec in enumerate(examen['secciones']):
    start_page += generate_background_html(sec,tpl,sec_num=i+1,start_page=start_page,path=path)
    await html2pdf(f"{sec['nombre']}-background.html",sleep_time=0,page=page,path=path)

def generate_sec_pdfs(examen,path=os.getcwd()):
  secciones = []
  for i,sec in enumerate(examen['secciones']):
    outname = f"{path}/{sec['nombre']}-{examen['versi??n']}-{examen['c??digo']}.pdf"
    subprocess.call(
      f"pdftk \"{path}/{sec['nombre']}.pdf\" multibackground \"{path}/{sec['nombre']}-background.pdf\" output \"{outname}\"",
      shell=True
    )
    secciones.append(outname)
  return secciones

async def generate(examen):
  jinja_env = jinja2.Environment(
    #donde est??n los templates, por defecto es la carpeta actual
    loader = jinja2.FileSystemLoader('templates'),autoescape= True
  )

  #copiar los assets a un directorio temporal
  pwd = tempfile.TemporaryDirectory()

  for file in os.listdir(f'assets'):
    shutil.copy(f'assets/{file}',f'{pwd.name}/{file}')
  
  df = load_files(examen)

  #generar archivo de estructura y archivo de claves
  ruta_estructura = generate_estructura(examen,df,path=pwd.name)
  ruta_clave = generate_anskey(examen,df,path=pwd.name)

  df = process_items(df)

  # Generar el html de cada ??tem
  item_tpl = jinja_env.get_template('item.tpl.html')
  df['html'] = df.apply(lambda x: render_item(item_tpl,x,examen),axis=1)

  # Objetos para convertir un html a pdf usando chromium
  browser = await get_browser()
  page = await browser.newPage()

  prueba_tpl = jinja_env.get_template('test.tpl.html')
  await generate_content(examen,df,prueba_tpl,page=page,path=pwd.name)

  background_tpl = jinja_env.get_template('background.tpl.html')
  await generate_backgrounds(examen,background_tpl,start_page=2,page=page,path=pwd.name)
  
  rutas = generate_sec_pdfs(examen,path=pwd.name)
  
  await page.close()
  await browser.close()

  ruta_final = merge_pdf(rutas,f"PRUEBA-{examen['versi??n']}-{examen['c??digo']}.pdf",path=pwd.name)
  
  rutas = rutas + [ruta_final,ruta_clave,ruta_estructura]
  
  ruta_zip = f"{pwd.name}/{examen['versi??n']}-{examen['c??digo']}.zip"
  with ZipFile(ruta_zip,'w') as z:
    for ruta_final in rutas:
      z.write(ruta_final,arcname=ruta_final.split('/')[-1])
  return ruta_zip,pwd

# Streamlit - para generar la "estructura" de la prueba

st.title('Diagramar prueba - FastTestWeb')

datos = st.container()

examen = {
  'versi??n': datos.text_input(
    'Versi??n',
    help='Es para el nombre de archivo y asignar los temas y subtemas en la estructura (CIENCIAS,LETRAS,ARTE)'
  ),
  'c??digo' : datos.number_input(
    'C??digo',
    value=0,
    format='%d',
    help='Dejar en 0 si se genera por primera vez, ingresar un c??digo si se desea mantener siempre la mismas claves'
  ),
  'resaltar_clave': datos.checkbox(
    'Resaltar clave',
    help='Resalta la clave en amarillo para la revisi??n'
  ),
  'nsecciones':datos.number_input(
    'N??mero de secciones',
    value=3,
    format='%d'
  ),
  'secciones': []
}
extra_styles = datos.checkbox(
  'Agregar estilos adicionales',
  help='Marcar si se desea agregar estilos adicionales al contenido de la prueba'
)

if extra_styles:
  examen['extra_css'] = datos.text_area(
    'CSS extra',
    help = 'Agregar CSS extra al contenido de la prueba'
  )
else:
  examen['extra_css'] = ''

if examen['c??digo'] == 0:
  examen['c??digo'] = int(time.time())

for i in range(examen['nsecciones']):
  container = st.container()
  container.subheader(f'Secci??n {i+1}')
  sec = {
    'archivo': container.file_uploader(
      f'Archivo {i+1}',
      help='El archivo de metadata exportado por FastTestWeb'
    ),
    'nombre': container.text_input(
      f'Nombre {i+1}',
      help='Esta etiqueta sale en la primera p??gina y tambien en los bordes'
    ),
    'tiempo': container.text_input(
      f'Tiempo {i+1}',
      help='Esta etiqueta sale en la primera p??gina'
    ),
    'saltos': container.text_input(
      f'Saltos de p??gina {i+1}',
      help='Indicar el n??mero de ??tem despu??s del cual se quiere insertar un salto de p??gina, separar por comas si se quiere indicar varios ej. 5,6,7'
    ),
    'blancas': container.text_input(
      f'P??ginas en blanco {i+1}',
      help='Indicar el n??mero de ??tem despu??s del cual se quiere insertar una p??gina en blanco, separar por comas si se quiere indicar varios ej. 5,6,7'
    ),
  }
  sec['saltos'] = [i for i in sec['saltos'].split(',') if sec['saltos']!='']
  sec['blancas'] = [int(i) for i in sec['blancas'].split(',') if sec['blancas']!='']
  examen['secciones'].append(sec)

submit = st.container()
resultados = st.container()
resultados.empty()

btn = submit.button('PROCESAR',on_click=lambda:procesar(resultados,examen))

def procesar(resultados,examen):
  with resultados:
    with st.spinner('Generando archivos...'):
      ruta_zip,tmp = asyncio.run(generate(examen))
      print(tmp.name)
      st.header("Archivos generados")
      with open(ruta_zip,'rb') as file:
        st.download_button(
          "Descargar Archivos",
          data=file,
          file_name=ruta_zip.split('/')[-1],
          mime="application/zip"
        )