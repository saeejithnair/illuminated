# Illuminated

Illuminated is a web application that allows users to extract images and their corresponding captions from arXiv publications.

## Features

- Extract images from arXiv publications using the paper's URL
- Download extracted images as a ZIP file
- Download image metadata (figure numbers and captions) as a JSON file

## Installation

1. Clone this repository
2. Create a virtual environment: `python -m venv venv`
3. Activate the virtual environment:
   - On Windows: `venv\Scripts\activate`
   - On macOS and Linux: `source venv/bin/activate`
4. Install the required packages: `pip install -r requirements.txt`

## Usage

1. Run the application: `python app.py`
2. Open a web browser and navigate to `http://localhost:5000`
3. Enter an arXiv URL (e.g., `https://arxiv.org/abs/2103.00020`)
4. Click "Extract Images"
5. Once processing is complete, use the provided links to download the images and JSON data

## License

This project is licensed under the MIT License.
