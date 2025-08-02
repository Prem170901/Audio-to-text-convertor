from flask import Flask, request, render_template, send_file, jsonify, send_from_directory
import whisper
import os
from pydub import AudioSegment
from fpdf import FPDF
from concurrent.futures import ThreadPoolExecutor
import time
import logging
from werkzeug.utils import secure_filename
import shutil
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


SMTP_SERVER = "smtp.mailersend.net"
SMTP_PORT = 587
SMTP_USERNAME = "MS_nW4yDb@trial-v69oxl5r1zxg785k.mlsender.net"
SMTP_PASSWORD = "NBken50zFuWUYkZP"

# Configure allowed file types
ALLOWED_EXTENSIONS = {'wav', 'mp3', 'ogg', 'm4a', 'flac', 'aac'}

# Create necessary directories
for directory in ['uploads', 'static']:
    if not os.path.exists(directory):
        os.makedirs(directory)

def send_email(recipient_email, pdf_path):
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USERNAME
        msg['To'] = recipient_email
        msg['Subject'] = "Vika Soft Audio Transcription"

        body = " Thank you for using our tool. Please find your audio transcription attached in my email."
        msg.attach(MIMEText(body, 'plain'))

        with open(pdf_path, "rb") as f:
            pdf_attachment = MIMEApplication(f.read(), _subtype="pdf")
            pdf_attachment.add_header('Content-Disposition', 'attachment', filename=os.path.basename(pdf_path))
            msg.attach(pdf_attachment)

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)

        return True
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}")
        return False

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def cleanup_files(files):
    """Clean up temporary files"""
    for file in files:
        try:
            if os.path.exists(file):
                os.remove(file)
        except Exception as e:
            logger.error(f"Error cleaning up file {file}: {str(e)}")

def split_audio(file_path, chunk_length_ms=30 * 1000):
    try:
        audio = AudioSegment.from_wav(file_path)
        chunks = [audio[i:i + chunk_length_ms] for i in range(0, len(audio), chunk_length_ms)]
        return chunks
    except Exception as e:
        logger.error(f"Error splitting audio: {str(e)}")
        raise

def save_chunk(chunk, i):
    chunk_filename = f"uploads/chunk_{i}.wav"
    try:
        chunk.export(chunk_filename, format="wav")
        return chunk_filename
    except Exception as e:
        logger.error(f"Error saving chunk {i}: {str(e)}")
        raise

def transcribe_chunk(chunk_filename):
    try:
        model = whisper.load_model("medium")
        audio = whisper.load_audio(chunk_filename)
        audio = whisper.pad_or_trim(audio)
        mel = whisper.log_mel_spectrogram(audio).to(model.device)

        options = whisper.DecodingOptions(
            temperature=0.3,
            beam_size=5,
            fp16=False,
            language='en' 
        )
        result = whisper.decode(model, mel, options)
        return result.text
    except Exception as e:
        logger.error(f"Error transcribing chunk {chunk_filename}: {str(e)}")
        raise
    finally:
        cleanup_files([chunk_filename])

def save_transcription_to_pdf(transcription, pdf_filename="transcription.pdf"):
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_font("Arial", size=12)
        
        # Add header with timestamp
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, 'Audio Transcription', ln=True, align='C')
        pdf.set_font("Arial", 'I', 10)
        pdf.cell(0, 10, f'Generated on: {time.strftime("%Y-%m-%d %H:%M:%S")}', ln=True, align='R')
        pdf.set_font("Arial", size=12)
        
        # Add transcription content
        pdf.multi_cell(0, 10, transcription)
        pdf.output(pdf_filename)
    except Exception as e:
        logger.error(f"Error saving PDF: {str(e)}")
        raise

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download/<filename>')
def download(filename):
    try:
        return send_from_directory('static', filename, as_attachment=True)
    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        return jsonify({"error": "File not found"}), 404

@app.route('/upload', methods=['POST'])
def upload():
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files['file']
        recipient_email = request.form.get('email')
        
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400

        if not allowed_file(file.filename):
            return jsonify({"error": "File type not allowed"}), 400

        # Create a unique filename
        filename = secure_filename(f"{time.strftime('%Y%m%d_%H%M%S')}_{file.filename}")
        audio_path = os.path.join('uploads', filename)
        wav_audio_path = os.path.join('uploads', 'converted_audio.wav')
        
        temp_files = [audio_path, wav_audio_path]

        try:
            # Save and convert file
            file.save(audio_path)
            file_size = os.path.getsize(audio_path)
            is_large_file = file_size > 5 * 1024 * 1024   

            audio = AudioSegment.from_file(audio_path)
            audio.export(wav_audio_path, format="wav")

            # Process audio
            audio_chunks = split_audio(wav_audio_path)
            chunk_filenames = [save_chunk(chunk, i) for i, chunk in enumerate(audio_chunks)]
            temp_files.extend(chunk_filenames)

            # Transcribe chunks
            transcriptions = []
            with ThreadPoolExecutor() as executor:
                futures = [executor.submit(transcribe_chunk, chunk_filename) 
                          for chunk_filename in chunk_filenames]
                
                for future in futures:
                    transcriptions.append(future.result())

            # Generate PDF
            full_transcription = "\n\n".join(transcriptions)
            pdf_filename = os.path.join('static', f'transcription_{time.strftime("%Y%m%d_%H%M%S")}.pdf')
            save_transcription_to_pdf(full_transcription, pdf_filename=pdf_filename)

            if is_large_file:
                if not recipient_email:
                    return jsonify({"error": "Email required for large files"}), 400
                
                # Send email with the PDF
                if send_email(recipient_email, pdf_filename):
                    return jsonify({
                        "message": "Transcription completed and sent to your email",
                        "is_large_file": True
                    }), 200
                else:
                    return jsonify({"error": "Failed to send email"}), 500
            else:
                return jsonify({
                    "file_name": os.path.basename(pdf_filename),
                    "message": "Transcription completed successfully",
                    "is_large_file": False
                }), 200

        except Exception as e:
            logger.error(f"Error processing audio: {str(e)}")
            return jsonify({"error": str(e)}), 500

        finally:
            cleanup_files(temp_files)

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return jsonify({"error": "An unexpected error occurred"}), 500

# Cleanup function to run periodically (you can call this via a scheduler if needed)
def cleanup_old_files():
    try:
        # Clean up files older than 24 hours
        current_time = time.time()
        for directory in ['uploads', 'static']:
            for filename in os.listdir(directory):
                file_path = os.path.join(directory, filename)
                if os.path.getmtime(file_path) < current_time - 86400:  
                    cleanup_files([file_path])
    except Exception as e:
        logger.error(f"Error in cleanup: {str(e)}")

if __name__ == '__main__':
    shutil.rmtree('uploads')
    os.makedirs('uploads')
    app.run(host='0.0.0.0', port=3000, debug=True)