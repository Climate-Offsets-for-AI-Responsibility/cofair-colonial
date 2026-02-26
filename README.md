# Data Pipeline (the-colonial)

You can clone this repo into a local folder by running:
`git clone https://github.com/Climate-Offsets-for-AI-Responsibility/the-colonial.git`

This repo contains scripts and documentation for the Postgres database setup and ingestion.

If anything is unclear or not working correctly feel free to ask me (Andrew).

# Setup
*IMPORTANT* - Always run commands from the `the-colonial` directory!

Create a `.env` file in the root directory (`the-colonial`).

See `.env.example` for an example of what yours should look like, the info is also on trello!

Run the following code in your terminal to set up a virtual environment and install dependancies:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

If you don't have it, install Docker Desktop here: [Docker Desktop](https://www.docker.com/products/docker-desktop/)

Run this code to confirm it's installation:
```bash
docker --version
```
If you see a Docker version pop up, Docker is successfully installed.

## Docker Setup

- Open the Docker Desktop application on your computer or run `open -a Docker` in your terminal.
- Run `docker-compose up -d` to tell Docker to build its containers.
- Run `docker ps` to make sure its up and running. (You should see a table of containers)

TDLR: Everytime you start up docker just copy, paste, and run this code from the `the-colonial` directory:
```bash
open -a Docker
docker-compose up -d
docker ps  # Check if everything looks normal
```

&nbsp;
&nbsp;
&nbsp;

# Running the Insertion Scripts
This section includes the `scrape_pricing` and `donors_generate` scripts used to generate data, and then inserts that data into a Postgres DB.

Before running, make sure the docker containers are running and all dependancies are installed.

This part is easy! Just run the following script from the `the-colonial` directory:
```bash
python3 build_db.py
```

After this code runs, all of the data should be inserted into the Neon DB through Netlify

# Inspecting the Database (You can ignore this for now! It doesnt apply to the Neon DB that we'll be using)
### Postgres Setup
After the data has been inserted, you can check if everythign worked correctly by looking inside the database.

First execute this command to enter the Postgres container:
```bash
# Opens a running dbt container in your terminal, so youc an execute commands there
docker-compose exec postgres bash
# enter exit to quit
```

You are now inside the Docker container for Postgres.
From here you can run the following from inside the container to start Postgres: 
```bash
psql -U postgres
# enter \q to quit
```

You should see a prompt like `postgres=#`, meaning you can now enter postgres commands to navigate the database.
Some useful commands:

- `\l` - lists all databases (We'll use `cofair_db`)
- `\c` - connects to a database (e.g. `\c cofair_db`)
- `dn` - lists all schemas in a database
- `dt` - lists all tables in a database
- SQL commands like `SELECT * FROM schema.table`

## Database Architecture (in progress...)
```
cofair_db
├── raw (schema)
│    └── pricing_json (table)
│    └── donors_csv (table)
│
├── staged (schema)

```
