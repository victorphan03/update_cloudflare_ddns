import requests
import json
import os
import logging
import socket
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def read_config(config_file_path):
    """Reads a JSON configuration file."""
    try:
        with open(config_file_path, 'r') as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        logging.error(f"Configuration file not found: {config_file_path}")
        return None
    except json.JSONDecodeError:
        logging.error(f"Invalid JSON format in configuration file: {config_file_path}")
        return None
    except Exception as e:
        logging.error(f"An error occurred while reading the configuration file: {e}")
        return None

def write_config(config_file_path, config):
    """Writes a JSON configuration file."""
    try:
        with open(config_file_path, 'w') as f:
            json.dump(config, f, indent=4)
        logging.info(f"Configuration file updated: {config_file_path}")
    except Exception as e:
        logging.error(f"An error occurred while writing the configuration file: {e}")

def get_public_ip():
    """Retrieves the public IP address."""
    try:
        response = requests.get('https://api.ipify.org?format=json')
        response.raise_for_status()
        return response.json()['ip']
    except requests.exceptions.RequestException as e:
        logging.error(f"Error getting public IP: {e}")
        return None

def get_all_a_records(zone_id, api_token):
    """Retrieves all A records from Cloudflare."""
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=A"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        records = response.json().get("result", [])
        return records
    except requests.exceptions.RequestException as e:
        logging.error(f"Error retrieving DNS records: {e}")
        return []
    except json.JSONDecodeError:
        logging.error("Invalid JSON response from Cloudflare API.")
        return []
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return []

def update_config_with_a_records(config_file_path, zone_id, api_token):
    """Updates the configuration file with all A records from Cloudflare."""
    config = read_config(config_file_path)
    if not config:
        return

    records = get_all_a_records(zone_id, api_token)
    dns_records = [{"record_name": record["name"].split('.')[0]} for record in records]
    config["dns_records"] = dns_records

    write_config(config_file_path, config)

def get_record_id(zone_id, api_token, record_name):
    """Retrieves the Cloudflare DNS record ID for a given record name."""
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        records = response.json().get("result", [])

        for record in records:
            if record.get("name") == record_name:
                return record.get("id")

        logging.warning(f"DNS record '{record_name}' not found.")
        return None

    except requests.exceptions.RequestException as e:
        logging.error(f"Error retrieving DNS records: {e}")
        return None
    except json.JSONDecodeError:
        logging.error("Invalid JSON response from Cloudflare API.")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return None

def get_record_details(zone_id, record_id, api_token):
    """Retrieves the details for a given DNS record."""
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        record = response.json().get("result", {})
        return record.get("ttl"), record.get("proxied")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error retrieving DNS record details: {e}")
        return None, None

def update_cloudflare_dns(zone_id, record_id, record_name, new_ip, api_token):
    """Updates a Cloudflare DNS record."""
    ttl, proxied = get_record_details(zone_id, record_id, api_token)
    if ttl is None or proxied is None:
        logging.error(f"Could not retrieve details for record '{record_name}'.")
        return

    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    data = {
        "type": "A",
        "name": record_name,
        "content": new_ip,
        "ttl": ttl,
        "proxied": proxied,
    }

    try:
        response = requests.put(url, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        if result["success"]:
            logging.info(f"DNS record '{record_name}' updated to {new_ip}")
        else:
            logging.error(f"Failed to update DNS record: {result}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error updating Cloudflare DNS: {e}")

def main():
    """Main function to update Cloudflare DDNS."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_file_path = os.path.join(script_dir, "config.json")

    config = read_config(config_file_path)

    if not config:
        return

    zone_id = config.get("cloudflare_zone_id")
    api_token = config.get("cloudflare_api_token")
    dns_records = config.get("dns_records", [])
    update_interval = config.get("update_interval", 900)  # Default to 15 minutes if not specified
    domain_name = "victorphan.net"  # Add your domain name here

    if not all([zone_id, api_token, dns_records]):
        logging.error("Missing required configuration values in config.json.")
        return

    # Update the configuration file with all A records from Cloudflare
    update_config_with_a_records(config_file_path, zone_id, api_token)

    while True:
        public_ip = get_public_ip()
        if public_ip:
            for record in dns_records:
                subdomain = record.get("record_name")
                record_name = f"{subdomain}.{domain_name}"

                if not subdomain:
                    logging.error("Missing record_name in config.json dns_records.")
                    continue

                record_id = get_record_id(zone_id, api_token, record_name)

                if not record_id:
                    continue

                try:
                    current_dns_ip = socket.gethostbyname(record_name)
                    if current_dns_ip != public_ip:
                        update_cloudflare_dns(zone_id, record_id, record_name, public_ip, api_token)
                    else:
                        logging.info(f"IP address has not changed for {record_name}. Current IP: {public_ip}")
                except socket.gaierror:
                    logging.error(f"Could not resolve {record_name}. Attempting to update anyway.")
                    update_cloudflare_dns(zone_id, record_id, record_name, public_ip, api_token)

        logging.info(f"Sleeping for {update_interval} seconds before next update.")
        time.sleep(update_interval)

if __name__ == "__main__":
    main()
