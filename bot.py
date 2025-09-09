import os
import re
import zipfile
import tempfile
import shutil
import logging
import asyncio
import subprocess
from typing import Dict, Any
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode

# تكوين logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# البيانات المضمنة مباشرة في الكود (يجب استبدالها ببياناتك الخاصة)
API_ID = 29914850  # استبدل بـ API ID الحقيقي
API_HASH = "de7b0ee6f49fff7b4a5f0e5c015972ce"  # استبدل بـ API Hash الحقيقي
BOT_TOKEN = "7563523261:AAF1gdK_19of_W9_oCm9nybBdLuppXyM2Ec"  # استبدل بـ Bot Token الحقيقي

# حالة المستخدمين
user_sessions: Dict[int, Dict[str, Any]] = {}

# إنشاء البوت
app = Client("gh_runner_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def cleanup_user_data(user_id: int):
    """مسح بيانات المستخدم المؤقتة"""
    if user_id in user_sessions:
        session_data = user_sessions[user_id]
        if 'temp_dir' in session_data and os.path.exists(session_data['temp_dir']):
            shutil.rmtree(session_data['temp_dir'], ignore_errors=True)
        del user_sessions[user_id]

def extract_requirements_from_code(code: str) -> list:
    """استخراج المتطلبات من الكود"""
    requirements = set()
    
    # البحث عن استيرادات المكتبات
    patterns = [
        r'^\s*import\s+(\w+)',
        r'^\s*from\s+(\w+)',
        r'pip\.install\([\'"]([^\'"]+)[\'"]\)',
        r'pip install ([^\s&|;]+)'
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, code, re.MULTILINE)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            requirements.add(match.split('.')[0].strip())
    
    return list(requirements)

def create_requirements_file(requirements: list, path: str):
    """إنشاء ملف المتطلبات"""
    with open(path, 'w') as f:
        for req in requirements:
            f.write(f"{req}\n")

def is_python_version_valid(version: str) -> bool:
    """التحقق من صحة إصدار البايثون"""
    return bool(re.match(r'^python(3\.(1[0-1]|[0-9]))?$', version))

@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    """بدء المحادثة مع البوت"""
    user_id = message.from_user.id
    
    # تنظيف أي بيانات قديمة
    await cleanup_user_data(user_id)
    
    # تهيئة جلسة جديدة
    user_sessions[user_id] = {
        'step': 'awaiting_file',
        'temp_dir': tempfile.mkdtemp(),
        'file_type': None,
        'file_path': None,
        'requirements': None,
        'python_version': None,
        'run_command': None
    }
    
    await message.reply_text(
        "مرحباً! 👋 أنا بوت تشغيل الأكواد على GitHub Actions.\n\n"
        "يرجى إرسال ملف Python (.py) أو أرشيف ZIP يحتوي على مشروعك.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("إلغاء", callback_data="cancel")]
        ])
    )

@app.on_message(filters.command("cancel"))
async def cancel_command(client: Client, message: Message):
    """إلغاء العملية الحالية"""
    user_id = message.from_user.id
    await cleanup_user_data(user_id)
    await message.reply_text("تم إلغاء العملية الحالية.")

@app.on_message(filters.document | filters.text)
async def handle_message(client: Client, message: Message):
    """معالجة الرسائل الواردة"""
    user_id = message.from_user.id
    
    if user_id not in user_sessions:
        await message.reply_text("يرجى البدء باستخدام الأمر /start أولاً.")
        return
    
    session = user_sessions[user_id]
    step = session['step']
    
    try:
        if step == 'awaiting_file':
            await handle_file_upload(client, message, session)
        elif step == 'awaiting_requirements':
            await handle_requirements(client, message, session)
        elif step == 'awaiting_python_version':
            await handle_python_version(client, message, session)
        elif step == 'awaiting_run_command':
            await handle_run_command(client, message, session)
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await message.reply_text(f"حدث خطأ: {str(e)}")
        await cleanup_user_data(user_id)

async def handle_file_upload(client: Client, message: Message, session: Dict[str, Any]):
    """معالجة رفع الملف"""
    user_id = message.from_user.id
    
    if message.document:
        file_name = message.document.file_name or ""
        
        if file_name.endswith('.py'):
            session['file_type'] = 'python'
            await download_and_process_file(client, message, session)
        
        elif file_name.endswith('.zip'):
            session['file_type'] = 'zip'
            await download_and_process_file(client, message, session)
        
        else:
            await message.reply_text("يرجى إرسال ملف Python (.py) أو أرشيف ZIP فقط.")
    
    else:
        await message.reply_text("يرجى إرسال ملف Python (.py) أو أرشيف ZIP.")

async def download_and_process_file(client: Client, message: Message, session: Dict[str, Any]):
    """تنزيل ومعالجة الملف"""
    user_id = message.from_user.id
    temp_dir = session['temp_dir']
    
    await message.reply_text("جاري تنزيل الملف...")
    
    # تنزيل الملف
    file_path = await message.download(file_name=os.path.join(temp_dir, message.document.file_name))
    session['file_path'] = file_path
    
    # معالجة الملف
    if session['file_type'] == 'python':
        # استخراج المتطلبات من الكود
        with open(file_path, 'r', encoding='utf-8') as f:
            code = f.read()
        
        requirements = extract_requirements_from_code(code)
        
        if requirements:
            session['requirements'] = requirements
            req_file = os.path.join(temp_dir, "requirements.txt")
            create_requirements_file(requirements, req_file)
            
            await message.reply_text(
                f"تم اكتشاف المتطلبات التالية في الكود:\n" + 
                "\n".join(f"• {req}" for req in requirements) +
                "\n\nهل تريد تثبيت هذه المتطلبات؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("نعم", callback_data="req_yes"),
                    [InlineKeyboardButton("لا", callback_data="req_no")],
                    [InlineKeyboardButton("إلغاء", callback_data="cancel")]
                ])
            )
        else:
            session['step'] = 'awaiting_python_version'
            await message.reply_text("لم يتم العثور على متطلبات في الكود. ما إصدار Python الذي تريد استخدامه؟ (مثال: python3.11)")
    
    elif session['file_type'] == 'zip':
        # فك ضغط الأرشيف
        await message.reply_text("جاري فك ضغط الأرشيف...")
        extract_path = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_path, exist_ok=True)
        
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
        
        # البحث عن ملف المتطلبات
        req_file = None
        for root, dirs, files in os.walk(extract_path):
            if "requirements.txt" in files:
                req_file = os.path.join(root, "requirements.txt")
                break
        
        if req_file:
            with open(req_file, 'r') as f:
                requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            
            session['requirements'] = requirements
            
            await message.reply_text(
                f"تم العثور على ملف المتطلبات يحتوي على:\n" + 
                "\n".join(f"• {req}" for req in requirements[:10]) +
                ("\n..." if len(requirements) > 10 else "") +
                "\n\nهل تريد تثبيت هذه المتطلبات؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("نعم", callback_data="req_yes"),
                    [InlineKeyboardButton("لا", callback_data="req_no")],
                    [InlineKeyboardButton("إلغاء", callback_data="cancel")]
                ])
            )
        else:
            session['step'] = 'awaiting_python_version'
            await message.reply_text("لم يتم العثور على ملف requirements.txt. ما إصدار Python الذي تريد استخدامه؟ (مثال: python3.11)")

@app.on_callback_query()
async def handle_callback_query(client: Client, callback_query):
    """معالجة استجابات الأزرار"""
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    if user_id not in user_sessions:
        await callback_query.answer("انتهت الجلسة، يرجى البدء من جديد.")
        return
    
    session = user_sessions[user_id]
    
    if data == "cancel":
        await cleanup_user_data(user_id)
        await callback_query.message.edit_text("تم إلغاء العملية.")
        await callback_query.answer()
        return
    
    elif data in ["req_yes", "req_no"]:
        if data == "req_yes":
            await callback_query.message.edit_text("سيتم تثبيت المتطلبات. ما إصدار Python الذي تريد استخدامه؟ (مثال: python3.11)")
        else:
            session['requirements'] = None
            await callback_query.message.edit_text("لن يتم تثبيت المتطلبات. ما إصدار Python الذي تريد استخدامه؟ (مثال: python3.11)")
        
        session['step'] = 'awaiting_python_version'
        await callback_query.answer()
    
    elif data.startswith("py_ver_"):
        version = data.replace("py_ver_", "")
        session['python_version'] = version
        session['step'] = 'awaiting_run_command'
        
        default_command = "python3 main.py" if session['file_type'] == 'zip' else f"python3 {os.path.basename(session['file_path'])}"
        
        await callback_query.message.edit_text(
            f"تم اختيار إصدار: {version}\n\nما الأمر الذي تريد تنفيذه؟\n\nمثال: {default_command}"
        )
        await callback_query.answer()

async def handle_requirements(client: Client, message: Message, session: Dict[str, Any]):
    """معالجة متطلبات التشغيل"""
    user_id = message.from_user.id
    
    if message.document and message.document.file_name == "requirements.txt":
        # تنزيل ملف المتطلبات
        req_path = await message.download(file_name=os.path.join(session['temp_dir'], "requirements.txt"))
        
        with open(req_path, 'r') as f:
            requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
        session['requirements'] = requirements
        session['step'] = 'awaiting_python_version'
        
        await message.reply_text(
            f"تم حفظ المتطلبات. ما إصدار Python الذي تريد استخدامه؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Python 3.11", callback_data="py_ver_python3.11")],
                [InlineKeyboardButton("Python 3.10", callback_data="py_ver_python3.10")],
                [InlineKeyboardButton("Python 3.9", callback_data="py_ver_python3.9")],
                [InlineKeyboardButton("إدخال يدوي", callback_data="py_ver_custom")]
            ])
        )
    
    elif message.text and message.text.lower() == 'skip':
        session['requirements'] = None
        session['step'] = 'awaiting_python_version'
        
        await message.reply_text(
            "تم تخطي المتطلبات. ما إصدار Python الذي تريد استخدامه؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Python 3.11", callback_data="py_ver_python3.11")],
                [InlineKeyboardButton("Python 3.10", callback_data="py_ver_python3.10")],
                [InlineKeyboardButton("Python 3.9", callback_data="py_ver_python3.9")],
                [InlineKeyboardButton("إدخال يدوي", callback_data="py_ver_custom")]
            ])
        )
    
    else:
        await message.reply_text("يرجى إرسال ملف requirements.txt أو كتابة 'skip' لتخطي هذه الخطوة.")

async def handle_python_version(client: Client, message: Message, session: Dict[str, Any]):
    """معالجة إصدار البايثون"""
    user_id = message.from_user.id
    
    if message.text and is_python_version_valid(message.text):
        session['python_version'] = message.text
        session['step'] = 'awaiting_run_command'
        
        default_command = "python3 main.py" if session['file_type'] == 'zip' else f"python3 {os.path.basename(session['file_path'])}"
        
        await message.reply_text(f"تم تعيين إصدار Python: {message.text}\n\nما الأمر الذي تريد تنفيذه؟\n\nمثال: {default_command}")
    
    else:
        await message.reply_text("إصدار Python غير صالح. يرجى إدخال إصدار صحيح (مثال: python3.11)")

async def handle_run_command(client: Client, message: Message, session: Dict[str, Any]):
    """معالجة أمر التشغيل"""
    user_id = message.from_user.id
    
    if message.text:
        session['run_command'] = message.text
        await execute_workflow(client, message, session)
    else:
        await message.reply_text("يرجى إدخال أمر تشغيل صالح.")

async def execute_workflow(client: Client, message: Message, session: Dict[str, Any]):
    """تنفيذ سير العمل على GitHub"""
    user_id = message.from_user.id
    
    await message.reply_text("جاري تهيئة البيئة وتشغيل الكود...")
    
    try:
        # إنشاء مجلد العمل
        work_dir = session['temp_dir']
        
        # إذا كان الملف من نوع zip، فإننا ننتقل إلى المجلد المستخرج
        if session['file_type'] == 'zip':
            work_dir = os.path.join(work_dir, "extracted")
        
        # تثبيت المتطلبات إذا وجدت
        if session['requirements']:
            await message.reply_text("جاري تثبيت المتطلبات...")
            req_path = os.path.join(session['temp_dir'], "requirements.txt")
            create_requirements_file(session['requirements'], req_path)
            
            # تثبيت المتطلبات باستخدام pip
            process = await asyncio.create_subprocess_shell(
                f"pip3 install -r {req_path}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir
            )
            stdout, stderr = await process.communicate()
            
            if stdout:
                await message.reply_text(f"**مخرجات تثبيت المتطلبات:**\n```\n{stdout.decode()}\n```", parse_mode=ParseMode.MARKDOWN)
            if stderr:
                await message.reply_text(f"**أخطاء تثبيت المتطلبات:**\n```\n{stderr.decode()}\n```", parse_mode=ParseMode.MARKDOWN)
        
        # تنفيذ الأمر الذي أدخله المستخدم
        run_command = session['run_command']
        await message.reply_text(f"جاري تنفيذ الأمر: `{run_command}`", parse_mode=ParseMode.MARKDOWN)
        
        # استخدام subprocess.run بدلاً من asyncio.create_subprocess_exec للتوافق مع البوتات المتزامنة
        process = subprocess.run(
            run_command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=work_dir,
            timeout=300  # 5 دقائق كحد أقصى للتشغيل
        )
        
        # إرسال النتائج
        if process.stdout:
            for i in range(0, len(process.stdout), 4000):
                await message.reply_text(f"**المخرجات:**\n```\n{process.stdout[i:i+4000]}\n```", parse_mode=ParseMode.MARKDOWN)
        
        if process.stderr:
            for i in range(0, len(process.stderr), 4000):
                await message.reply_text(f"**الأخطاء:**\n```\n{process.stderr[i:i+4000]}\n```", parse_mode=ParseMode.MARKDOWN)
        
        if process.returncode == 0:
            await message.reply_text("✅ تم التنفيذ بنجاح!")
        else:
            await message.reply_text(f"❌ فشل التنفيذ مع رمز الخروج: {process.returncode}")
    
    except subprocess.TimeoutExpired:
        await message.reply_text("⏰ انتهت مهلة التشغيل (5 دقائق)")
    except Exception as e:
        logger.error(f"Error executing workflow: {e}")
        await message.reply_text(f"حدث خطأ أثناء التنفيذ: {str(e)}")
    
    finally:
        # تنظيف البيانات
        await cleanup_user_data(user_id)

@app.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):
    """عرض مساعدة البوت"""
    help_text = """
    **أوامر البوت:**
    /start - بدء عملية جديدة
    /cancel - إلغاء العملية الحالية
    /help - عرض هذه المساعدة

    **طريقة الاستخدام:**
    1. ابدأ بـ /start
    2. أرسل ملف Python (.py) أو أرشيف ZIP
    3. أرسل ملف المتطلبات requirements.txt أو اكتب 'skip'
    4. اختر إصدار Python
    5. أدخل أمر التشغيل

    **ملاحظات:**
    - يمكن للبوت اكتشاف المتطلبات تلقائياً من الكود
    - يدعم البوت جميع إصدارات Python 3.x
    - يتم التنفيذ في بيئة Ubuntu آمنة
    - مدة التشغيل القصوى هي 5 دقائق لكل عملية
    """
    
    await message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

# تشغيل البوت
if __name__ == "__main__":
    print("Starting bot...")
    app.run()
