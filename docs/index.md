[![Zou Logo](zou.png)](https://github.com/cgwire/zou)

# Welcome to the Zou documentation

Zou is an API that allows to store and manage the data of your CG production.
Through it you can link all the tools of your pipeline and make sure they are
all synchronized. 

To integrate it quickly in your tools you can rely on the dedicated Python client 
named [Gazu](https://gazu.cg-wire.com). 

The source is available on [Github](https://github.com/cgwire/cgwire-api).

# Who is it for?

The audience for Zou is made of Technical Directors, ITs and
Software Engineers from CG studios. With Zou they can enhance the
tools they provide to all departments.

# Features 

Zou can:

* Store production data: projects, shots, assets, tasks, files
  metadata and validations.
* Provide folder and file paths for any task.
* Data import from Shotgun or CSV files.
* Export main data to CSV files.
* Provide helpers to manage task workflow (start, publish, retake).
* Provide an event system to plug external modules on it.

# Install 

## Pre-requisites

The installation requires:

* An up and running Postgres instance (version >= 9.2)
* Python (version >= 2.7, version 3 is prefered)
* A Nginx instance
* Uwsgi

## Setup


### Dependecies

First let's install third parties software:

```bash
sudo apt-get install postgresql postgresql-client libpq-dev
sudo apt-get install python3 python3-pip python3-dev
sudo apt-get install libffi-dev libjpeg-dev git
sudo apt-get install nginx uwsgi
```

*NB: We recommend to install postgres in a separate machine.*


### Get sources

Create zou user:

```bash
sudo useradd --disabled-password --home /opt/zou zou 
```

Get sources:

```bash
cd /opt/
sudo git clone https://github.com/cgwire/zou.git
```

Install Python dependencies:

```
sudo pip3 install virtualenv
cd zou
virtualenv zouenv
. zouenv/bin/activate
sudo zouenv/bin/python3 setup.py install
sudo chown -R zou:www-data .
```


### Prepare database

Create Zou database in postgres:

```
sudo su -l postgres
psql -c 'create database zoudb;' -U postgres
```

Set a password for your postgres user. For that start the Postgres CLI:

```bash
psql
```

Then set the password (*mysecretpassword* if you want to do some tests).

```bash
psql (9.4.12)
Type "help" for help.

postgres=# \password postgres
Enter new password: 
Enter it again: 
```

Then exit from the postgres client console.

Finally, create database tables (it is required to leave the posgres console
and to activate the Zou virtual environment):

```
# Run it in your bash console.
zou init_db
```


### Configure Uwsgi

We need to run the application through `uwsgi`. So, let's write the `uwsgi` configuration:

*Path: /etc/zou/uwsgi.ini*

```
[uwsgi]
module = wsgi

master = true
processes = 5

socket = zou.sock
chmod-socket = 660
vacuum = true

die-on-term = true
```

Then we daemonize `uwsgi` via Systemd. For that we add a new 
file that will add a new daemon to be managed by Systemd:

*Path: /etc/systemd/system/zou.service*

```
[Unit]
Description=uWSGI instance to serve the Zou API
After=network.target

[Service]
User=zou
Group=www-data
WorkingDirectory=/opt/zou
Environment="PATH=/opt/zou/zouenv/bin"
ExecStart=/opt/zou/zouenv/bin/uwsgi --ini /etc/zou/uwsgi.ini

[Install]
WantedBy=multi-user.target
```


### Configure Nginx

Finally we serve the API through a Nginx server. For that, add this
configuration file to Nginx to redirect the traffic to the *uwsgi* daemon:

*Path: /etc/nginx/sites-available/zou*

```
server {
    listen 80;
    server_name server_domain_or_IP;

    location / {
        include uwsgi_params;
        uwsgi_pass unix:/tmp/zou.sock;
    }
}
```

*NB: We use the 80 port here to make this documentation simpler but the 443 port and https connection are highly recommended.*

Make sure too that default configuration is removed: 

```bash
sudo rm /etc/nginx/sites-enabled/default
```


We enable that Nginx configuration with this command:

```bash
sudo ln -s /etc/nginx/sites-available/zou /etc/nginx/sites-enabled
```

Finally we can start our daemon and restart Nginx:

```bash
sudo service zou start
sudo service nginx restart
```

## Admin users

To start with Zou you need to add an admin user. This user will be able to to
log in and to create other users. For that go into the terminal and run the
`zou` binary:

```
zou create_admin
```

It will ask for an email and a password. Then your user will be created with
the name "Super Admin".


Another option is to disable logins by setting the environment variable `LOGIN_DISABLED` to
    `True`. Then you will be able to perform user creations. But we discourage doing it.

# Configuration 

To run properly, Zou requires a bunch of parameters you can give through
environment variables. These variables can be set in your systemd script. 
All variables are listed in the [configuration
section](configuration).

# Available actions

To know more about what is possible to do with the CGWire API, refer to the
[API section](api).


# About authors

Zou is written by CG Wire, a company based in France. We help small to
midsize CG studios to manage their production and build pipeline efficiently.

We apply software craftmanship principles as much as possible. We love
coding and consider that strong quality and good developer experience matter a lot.
Our extensive experience allows studios to get better at doing software and focus
more on the artistic work.

Visit [cg-wire.com](https://cg-wire.com) for more information.

[![CGWire Logo](cgwire.png)](https://cgwire.com)