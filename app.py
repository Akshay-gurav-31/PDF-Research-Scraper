from flask import Flask, render_template, request, send_file, jsonify, Response
import os
import sys
import threading
import queue
import time
import argparse
import tempfile
import shutil
import re
import json
from scrape_pdfs import main as scrape_pdfs_main
import requests
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

CROSSREF_API = "https://api.crossref.org/works"
UNPAYWALL_API_TEMPLATE = "https://api.unpaywall.org/v2/{doi}?email={email}"

# Google Gemini API Key from environment variable
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

app = Flask(__name__)

# Store job status and results
jobs = {}
# Store output queues for real-time streaming
output_queues = {}
# Thread pool for managing concurrent jobs
executor = ThreadPoolExecutor(max_workers=3)

class ArgumentParserError(Exception):
    """Custom exception for argument parsing errors"""
    pass

class ThrowingArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser that throws exceptions instead of exiting"""
    def error(self, message):
        raise ArgumentParserError(message)

class StreamToQueue:
    """Custom stream that writes to a queue for real-time output"""
    def __init__(self, q):
        self.queue = q
        
    def write(self, data):
        self.queue.put(data)
        
    def flush(self):
        pass

def run_scraper(args_dict, job_id):
    """Run the PDF scraper with given arguments"""
    temp_dir = None
    output_queue = queue.Queue()
    output_queues[job_id] = output_queue
    
    try:
        # Generate topics and keywords from the description
        description = args_dict.get('keywords', '')
        topics_keywords = generate_keywords_from_description(description)
        
        # Ensure we have at least one topic
        if not topics_keywords:
            topics_keywords = {"main_topic": description}
        
        # Process all topics and scrape PDFs
        email = args_dict.get('email', '')
        max_pdfs = int(args_dict.get('max', 20))  # Ensure it's an integer
        num_topics = len(topics_keywords)
        if num_topics == 0:
            num_topics = 1
        max_pdfs_per_topic = max(5, max_pdfs // num_topics)  # Distribute max PDFs across topics
        
        # Process topics and scrape
        all_results = process_topics_and_scrape(topics_keywords, email, max_pdfs_per_topic)
        
        # Collect all outputs and PDF counts
        total_output = ""
        total_pdf_count = 0
        topic_details = {}
        
        for topic, result in all_results.items():
            if 'log' in result:
                total_output += f"\n--- {topic} ---\n{result['log']}\n"
            total_pdf_count += result.get('pdf_count', 0)
            topic_details[topic] = {
                'pdf_count': result.get('pdf_count', 0),
                'output_dir': result.get('output_dir', '')
            }
        
        # Create a main directory for all results
        main_temp_dir = tempfile.mkdtemp(prefix=f"pdf_scraper_{job_id}_")
        
        # Move all PDFs from topic directories to main directory
        for topic, result in all_results.items():
            output_dir = result.get('output_dir', '')
            if output_dir and os.path.exists(output_dir):
                # Move all PDF files to main directory
                for file in os.listdir(output_dir):
                    if file.endswith('.pdf'):
                        src_path = os.path.join(output_dir, file)
                        dest_path = os.path.join(main_temp_dir, file)
                        try:
                            shutil.move(src_path, dest_path)
                        except Exception as e:
                            print(f"Error moving file {file}: {e}")
        
        # Store job results
        jobs[job_id] = {
            'status': 'completed',
            'output_dir': main_temp_dir,
            'log': total_output,
            'pdf_count': total_pdf_count,
            'requested_count': max_pdfs,
            'topics': topic_details
        }
        
        # Log information about delivery
        if total_pdf_count < max_pdfs:
            output_queue.put(f"\nWarning: Only able to deliver {total_pdf_count} out of {max_pdfs} requested PDFs.\n")
            output_queue.put("This is due to limited availability of Open Access PDFs for your search terms.\n")
        else:
            output_queue.put(f"\nSuccessfully delivered {total_pdf_count} PDFs as requested.\n")
        
    except Exception as e:
        # Clean up temp directory if it was created
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
        
        # Collect all output
        output = ""
        while not output_queue.empty():
            try:
                output += output_queue.get_nowait()
            except queue.Empty:
                break
        
        jobs[job_id] = {
            'status': 'error',
            'error': str(e),
            'log': output
        }
    finally:
        # Clean up the output queue
        if job_id in output_queues:
            del output_queues[job_id]

@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')

@app.route('/scrape', methods=['POST'])
def start_scraping():
    """Start a PDF scraping job"""
    data = request.get_json()
    
    # Generate keywords from the description if provided
    if 'keywords' in data:
        # Generate better keywords using Gemini API
        generated_keywords = generate_keywords_from_description(data['keywords'])
        data['keywords'] = generated_keywords
        print(f"Generated keywords: {generated_keywords}")
    
    # Generate a job ID
    job_id = str(len(jobs) + 1)
    
    # Initialize job status
    jobs[job_id] = {
        'status': 'running',
        'log': 'Starting PDF scraping job...'
    }
    
    # Submit the scraping job to the thread pool
    future = executor.submit(run_scraper, data, job_id)
    
    return jsonify({'job_id': job_id})

@app.route('/status/<job_id>')
def get_status(job_id):
    """Get the status of a scraping job"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    return jsonify(jobs[job_id])

@app.route('/stream/<job_id>')
def stream_output(job_id):
    """Stream real-time output from a scraping job"""
    if job_id not in output_queues:
        return jsonify({'error': 'Job not found or not running'}), 404
    
    def generate():
        while True:
            # Check if job is still running
            if job_id not in jobs or jobs[job_id]['status'] not in ['running']:
                # Send final output if any
                if job_id in output_queues:
                    while not output_queues[job_id].empty():
                        try:
                            yield f"data: {output_queues[job_id].get_nowait()}\n\n"
                        except queue.Empty:
                            break
                yield "data: [JOB COMPLETED]\n\n"
                break
            
            # Get output from queue
            try:
                output = output_queues[job_id].get(timeout=1)
                yield f"data: {output}\n\n"
            except queue.Empty:
                # Send a keep-alive message
                yield "data: \n\n"
    
    return Response(generate(), mimetype='text/event-stream')

@app.route('/download/<job_id>')
def download_results(job_id):
    """Download the results of a completed job"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    if job['status'] != 'completed':
        return jsonify({'error': 'Job not completed yet'}), 400
    
    # Check if the output directory still exists
    output_dir = job.get('output_dir')
    if not output_dir or not os.path.exists(output_dir):
        return jsonify({'error': 'Output directory not found'}), 404
    
    # Create a zip file of the output directory
    zip_path = f"{output_dir}.zip"
    
    shutil.make_archive(output_dir, 'zip', output_dir)
    
    return send_file(zip_path, as_attachment=True)

def generate_keywords_from_description(description):
    """Generate research keywords from a topic description using Google Gemini API"""
    try:
        # Prepare the prompt for Gemini to break down complex topics
        prompt = f"""
        You are an academic research assistant. A user has provided the following research topic description:
        
        "{description}"
        
        Please analyze this description and:
        1. Identify the main research topic
        2. Break it down into smaller, manageable sub-topics if it's complex
        3. For each sub-topic, generate 3-5 specific academic keywords that would be effective for searching research papers
        4. Format your response as a JSON object with sub-topics as keys and comma-separated keywords as values
        
        Example format:
        {{
            "sub-topic 1": "keyword1, keyword2, keyword3",
            "sub-topic 2": "keyword4, keyword5, keyword6"
        }}
        
        If the topic is simple, just provide one sub-topic with relevant keywords.
        Only return the JSON object, nothing else.
        """
        
        # Call the Gemini API with the new gemini-2.0-flash model
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        
        headers = {
            "Content-Type": "application/json"
        }
        
        data = {
            "contents": [{
                "parts": [{
                    "text": prompt
                }]
            }]
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        # Extract the response text
        if "candidates" in result and len(result["candidates"]) > 0:
            response_text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
            
            # Try to parse as JSON
            try:
                topics_keywords = json.loads(response_text)
                return topics_keywords
            except json.JSONDecodeError:
                # If JSON parsing fails, treat as a simple keyword list
                keywords = re.sub(r'\n+', ', ', response_text)
                keywords = re.sub(r',+', ',', keywords)
                keywords = keywords.strip(', ')
                if keywords:
                    return {"main_topic": keywords}
        
        # Fallback if no keywords were generated
        print("No structured keywords generated, using original description")
        return {"main_topic": description}
    except Exception as e:
        print(f"Error generating keywords: {e}")
        # Fallback to using the description as keywords
        return {"main_topic": description}

def process_topics_and_scrape(topics_keywords, email, max_pdfs_per_topic=10):
    """Process multiple topics and scrape PDFs for each"""
    all_results = {}
    
    try:
        for topic, keywords in topics_keywords.items():
            try:
                print(f"Processing topic: {topic}")
                print(f"Keywords: {keywords}")
                
                # Create a temporary directory for this topic
                temp_dir = tempfile.mkdtemp(prefix=f"topic_{topic}_")
                
                # Create argument parser similar to the original script
                parser = ThrowingArgumentParser()
                parser.add_argument("--keywords", "-k", type=str, required=True)
                parser.add_argument("--email", "-e", type=str, required=True)
                parser.add_argument("--max", "-m", type=int, default=10)
                parser.add_argument("--outdir", "-o", type=str, default="downloaded_pdfs")
                parser.add_argument("--start_date", type=str, default="2024-01-01")
                parser.add_argument("--end_date", type=str, default="2025-12-31")
                parser.add_argument("--pause", type=float, default=2.0)
                parser.add_argument("--filter-publishers", action="store_true")
                
                # Build argument list
                arg_list = [
                    '--keywords', str(keywords),
                    '--email', email,
                    '--max', str(max_pdfs_per_topic),
                    '--outdir', temp_dir,
                    '--start_date', '2024-01-01',
                    '--end_date', '2025-12-31',
                    '--pause', '2.0'
                ]
                
                # Add filter publishers flag if needed (from original args)
                # For now, we'll set it to False by default
                # arg_list.append('--filter-publishers')
                
                # Parse arguments
                args = parser.parse_args(arg_list)
                
                # Capture print output
                old_stdout = sys.stdout
                output_queue = queue.Queue()
                sys.stdout = StreamToQueue(output_queue)
                
                # Run the scraper
                try:
                    scrape_pdfs_main(args)
                finally:
                    sys.stdout = old_stdout
                
                # Collect all output
                output = ""
                while not output_queue.empty():
                    try:
                        output += output_queue.get_nowait()
                    except queue.Empty:
                        break
                
                # Count valid PDF files
                pdf_files = []
                if os.path.exists(temp_dir):
                    pdf_files = [f for f in os.listdir(temp_dir) if f.endswith('.pdf')]
                pdf_count = len(pdf_files)
                
                # Store results for this topic
                all_results[topic] = {
                    'output_dir': temp_dir,
                    'log': output,
                    'pdf_count': pdf_count,
                    'pdf_files': pdf_files
                }
                
                print(f"Completed topic: {topic} - Found {pdf_count} PDFs")
                
            except Exception as e:
                print(f"Error processing topic {topic}: {e}")
                all_results[topic] = {
                    'error': str(e),
                    'pdf_count': 0
                }
    except Exception as e:
        print(f"Error in process_topics_and_scrape: {e}")
    
    return all_results

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)