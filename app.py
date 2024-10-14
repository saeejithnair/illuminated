import os
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
import requests
import tarfile
import re
from PIL import Image
import json
from io import BytesIO
import zipfile
from werkzeug.utils import secure_filename
import google.generativeai as genai
import tempfile

app = Flask(__name__)

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'tar.gz'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Configure Gemini API (you'll need to set up your API key)
genai.configure(api_key=os.environ["API_KEY"])

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def download_arxiv_source(arxiv_id):
    url = f"https://arxiv.org/e-print/{arxiv_id}"
    response = requests.get(url)
    if response.status_code == 200:
        content = BytesIO(response.content)
        
        # Try to open as gzip
        try:
            with tarfile.open(fileobj=content, mode="r:gz") as tar:
                return content, "tar"
        except tarfile.ReadError:
            content.seek(0)  # Reset file pointer
            
            # Try to open as zip
            try:
                with zipfile.ZipFile(content) as zip_ref:
                    return content, "zip"
            except zipfile.BadZipFile:
                content.seek(0)  # Reset file pointer
                
                # If it's neither gzip nor zip, return the raw content
                print("Warning: Source is neither gzip nor zip. Returning raw content.")
                return content, "raw"
    else:
        raise Exception(f"Failed to download arXiv source. Status code: {response.status_code}")

def download_arxiv_pdf(arxiv_id):
    url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    response = requests.get(url)
    if response.status_code == 200:
        return BytesIO(response.content)
    else:
        raise Exception(f"Failed to download arXiv PDF. Status code: {response.status_code}")

def extract_figures_from_pdf(pdf_content):
    model = genai.GenerativeModel("gemini-1.5-flash")
    
    pdf_file = genai.upload_file(pdf_content, mime_type="application/pdf")
    
    prompt = "Read this paper carefully and then generate the figure numbers and captions for each figure in the image. Do not miss anything or make up any information. Structure the output in JSON format as a list of dicts, each dict should have two keys: `figure_number` and `caption`."
    
    result = model.generate_content([prompt, pdf_file])
    print(f"PDF extraction result: {result.text}")
    
    json_match = re.search(r'```json\n(.*?)\n```', result.text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        json_str = result.text
    
    try:
        figure_info = json.loads(json_str)
        print(f"Extracted {len(figure_info)} figures")
        return figure_info
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON: {e}")
        print("Raw JSON string:", json_str)
        return []

def match_figures_with_latex(figure_info, latex_content):
    model = genai.GenerativeModel("gemini-1.5-flash")
    
    # Create a temporary file to store the LaTeX content
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.tex', delete=False) as temp_file:
        temp_file.write(latex_content)
        temp_file_path = temp_file.name

    try:
        latex_file = genai.upload_file(temp_file_path, mime_type="text/plain")
        
        prompt = f"""
        Given the following LaTeX source and extracted figure information, find the correct file paths for each figure.
        Return the results as a JSON list of dictionaries, where each dictionary contains 'figure_number', 'caption', and 'file_path'.
        
        Extracted figure information:
        {json.dumps(figure_info, indent=2)}
        
        LaTeX source:
        {latex_content[:1000]}  # Sending first 1000 characters as a sample
        
        Focus on finding \includegraphics commands that match the figure numbers or captions.
        """
        
        result = model.generate_content([prompt, latex_file])
        print(f"LaTeX matching result: {result.text}")
        
        json_match = re.search(r'```json\n(.*?)\n```', result.text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = result.text
        
        try:
            matched_figures = json.loads(json_str)
            print(f"Matched {len(matched_figures)} figures")
            return matched_figures
        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON: {e}")
            print("Raw JSON string:", json_str)
            return []
    finally:
        # Clean up the temporary file
        os.unlink(temp_file_path)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        arxiv_link = request.form['arxiv_link']
        arxiv_id = arxiv_link.split('/')[-1]
        
        try:
            # Download arXiv source
            source_content, source_format = download_arxiv_source(arxiv_id)
            
            # Download PDF separately
            pdf_content = download_arxiv_pdf(arxiv_id)
            
            # Extract LaTeX content
            latex_content = ""
            source_content.seek(0)  # Reset file pointer
            try:
                with tarfile.open(fileobj=source_content, mode="r:*") as tar:
                    for member in tar.getmembers():
                        if member.name.endswith('.tex'):
                            latex_content += tar.extractfile(member).read().decode('utf-8', errors='ignore')
            except tarfile.ReadError:
                source_content.seek(0)  # Reset file pointer
                try:
                    with zipfile.ZipFile(source_content) as zip_ref:
                        for name in zip_ref.namelist():
                            if name.endswith('.tex'):
                                with zip_ref.open(name) as file:
                                    latex_content += file.read().decode('utf-8', errors='ignore')
                except zipfile.BadZipFile:
                    print("Warning: Source is neither tar nor zip. Unable to extract LaTeX content.")
            
            if not latex_content:
                raise Exception("No LaTeX content found in the source files.")
            
            # Extract figures using Gemini
            figure_info = extract_figures_from_pdf(pdf_content)
            
            if not figure_info:
                return jsonify({
                    'status': 'warning',
                    'message': 'Failed to extract figures from the PDF.',
                    'arxiv_id': arxiv_id
                })
            
            # Match figures with LaTeX content
            matched_figures = match_figures_with_latex(figure_info, latex_content)
            
            if not matched_figures:
                return jsonify({
                    'status': 'warning',
                    'message': 'Figures were extracted, but could not be matched with images in the LaTeX source.',
                    'arxiv_id': arxiv_id,
                    'figure_info': figure_info
                })
            
            # Save images and prepare JSON data
            image_data = []
            for idx, figure in enumerate(matched_figures, 1):
                image_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{arxiv_id}_{idx}.png")
                
                # Extract and save the image from the source content
                source_content.seek(0)  # Reset file pointer
                if source_format == "tar":
                    with tarfile.open(fileobj=source_content, mode="r:*") as tar:
                        image_file = tar.extractfile(figure['file_path'])
                        if image_file:
                            with open(image_path, 'wb') as f:
                                f.write(image_file.read())
                elif source_format == "zip":
                    with zipfile.ZipFile(source_content) as zip_ref:
                        with zip_ref.open(figure['file_path']) as image_file:
                            with open(image_path, 'wb') as f:
                                f.write(image_file.read())
                else:
                    print(f"Warning: Could not extract image {figure['file_path']} from raw content")
                    continue

                image_data.append({
                    "figure_number": figure['figure_number'],
                    "filename": f"{arxiv_id}_{idx}.png",
                    "original_filename": figure['file_path'],
                    "caption": figure['caption']
                })
            
            # Save JSON data
            json_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{arxiv_id}_data.json")
            with open(json_path, 'w') as f:
                json.dump(image_data, f, indent=2)
            
            print(f"Processed {len(image_data)} images")
            return jsonify({
                'status': 'success',
                'message': f'Processing complete. Found {len(image_data)} images.',
                'arxiv_id': arxiv_id,
                'image_data': image_data
            })
        except Exception as e:
            print(f"Error processing request: {str(e)}")
            return jsonify({'status': 'error', 'message': str(e)})
    
    return render_template('index.html')

@app.route('/download_images/<arxiv_id>')
def download_images(arxiv_id):
    zip_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{arxiv_id}_images.zip")
    
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for file in os.listdir(app.config['UPLOAD_FOLDER']):
            if file.startswith(arxiv_id) and file.endswith('.png'):
                zipf.write(os.path.join(app.config['UPLOAD_FOLDER'], file), file)
    
    return send_file(zip_path, as_attachment=True)

@app.route('/download_json/<arxiv_id>')
def download_json(arxiv_id):
    json_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{arxiv_id}_data.json")
    return send_file(json_path, as_attachment=True)

# Add a new route to serve image files
@app.route('/uploads/<path:filename>')
def serve_image(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.run(debug=True)
