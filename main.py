from flask import Flask, render_template, request, session, redirect, url_for, send_from_directory
from sys import exit
from sqlalchemy import event
from sqlalchemy.exc import DatabaseError
from flask_socketio import join_room, leave_room,  SocketIO
from flask_sqlalchemy import SQLAlchemy
from argon2 import PasswordHasher
from sqlalchemy import select, update, delete
from datetime import datetime
from random import choice
from os import makedirs, path as pth
from urllib.parse import urlparse
from string import hexdigits


class ChatApp:
    def __init__(self):
        self.app = Flask(__name__)
        self.app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///database.db"
        self.app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            "module": __import__('sqlcipher_wrapper')
        }
        self.app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        self.db_password = input("Enter the database password: ")
        self.db = SQLAlchemy(self.app)

        with self.app.app_context():
            @event.listens_for(self.db.engine, 'connect')
            def set_sqlcipher_key(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute(f'PRAGMA KEY = "{self.db_password}";')
                cursor.close()

        self.ph = PasswordHasher()
        db = self.db
        class Channels(db.Model):
            __tablename__ = "channels"
            channel_id = db.Column(db.String(5), primary_key = True)
            channel_name = db.Column(db.String(20), nullable = False)
            channel_description = db.Column(db.String(255))
            password = db.Column(db.String(20), nullable = False)
            owner_id = db.Column(db.String(10), db.ForeignKey('users.user_id', onupdate="CASCADE", ondelete="CASCADE"), nullable = False)


            def __repr__(self): return f'<Channels {self.channel_id}, {self.channel_name}>'

        class Users(db.Model):
            __tablename__ = "users"
            user_id = db.Column(db.String(10), primary_key = True)
            username = db.Column(db.String(10), nullable = False, unique = True)
            channel_id = db.Column(db.String(5), db.ForeignKey('channels.channel_id', ondelete = "SET NULL"), nullable = True)
            password = db.Column(db.String(20), nullable = False)
            user_type = db.Column(db.String(6), nullable = False)

            def __repr__(self):
                return f'<Users {self.user_id}, {self.username}>'


        class Messages(db.Model):
            __tablename__ = "messages"
            sender_id = db.Column(db.String(10), db.ForeignKey('users.user_id', onupdate="CASCADE", ondelete="CASCADE"), nullable = False)
            messsage_id = db.Column(db.Integer, primary_key = True, autoincrement = True)
            channel_id = db.Column(db.String(5), db.ForeignKey('channels.channel_id', onupdate="CASCADE", ondelete="CASCADE"))
            timestamp = db.Column(db.DateTime, nullable = False)
            content = db.Column(db.String(255), nullable = False)
            message_type = db.Column(db.String(1))

            def __repr__(self):
                return f'<Messages {self.sender_id}: {self.content}, {self.message_type}>'

        self.Users : type[Users] = Users
        self.Channels = Channels
        self.Messages = Messages
        with self.app.app_context():
            try:
                self.db.create_all()
            except DatabaseError:
                print("WRONG PASSWORD PROVIDED")
                exit(1)
            except Exception:
                print("AN UNKNOWN EXCEPTION HAS BEEN ENCOUNTERED")
                exit(1)
        self.UPLOAD_FOLDER = 'uploads'
        makedirs(self.UPLOAD_FOLDER, exist_ok=True)
        self.app.config["SECRET_KEY"] = "a1b2c3d4e5"
        self.socketio = SocketIO(self.app)
        self._configure_routes()

    #returns True if the user is not logged in or has no recored in db
    def check_session(self):
        #session check
        if 'user_id' not in session or 'username' not in session or session['user_id'] in [None, ''] or session['username'] in [None, '']:
            return True
        with self.app.app_context():
            stmt = select(self.Users).where(self.Users.user_id == session['user_id'], self.Users.username == session['username'])
            users = self.db.session.execute(stmt).scalars().all()
        if len(users) == 0:
            return True
        return False

    def verify_password(self,original, given):
        try:
            if self.ph.verify(original, given) == True:
                return True
            return False
        except Exception as ex:
            return False

    def generate_unique_code(self, length, type):
        while True:
            code = ''
            #generate the random code first
            for _ in range(length):
                code += choice(hexdigits)
            #match the type of the code
            match type:
                #for user code
                case 'user_id':
                    stmt = select(self.Users).where(self.Users.user_id == code)
                #for channel code
                case 'channel_id':
                    stmt = select(self.Channels).where(self.Channels.channel_id == code)
                case _:
                    print("Unknown type of code has been asked for, the type is:", type)
                    return None
            with self.app.app_context():
                query = self.db.session.execute(stmt).scalars().all()

            #break and go out only if the random code is unique
            if len(query) == 0:
                break
        return code

    def run(self,host="127.0.0.1", port=5000):
        #add debug here if needed
        self.socketio.run(self.app, debug=True, host=host, port=port)

    def _configure_routes(self):
        #login page
        @self.app.route("/login", methods = ["GET", "POST"])
        def login():
            #redirect to welcome if username and user_id is already present
            if "username" in session and "user_id" in session:
                with self.app.app_context():
                    stmt = select(self.Users).where(self.Users.user_id == session['user_id'], self.Users.username == session['username'])
                    users = self.db.session.execute(stmt).scalars().all()
                if len(users) != 0:
                    return redirect(url_for("welcome_screen"))
                session.pop("username", None)
                session.pop("user_id", None)

            if request.args.get("error"):
                return render_template('login.html', error = request.args.get('error'))

            if request.args.get("info"):
                return render_template('login.html', info = request.args.get('info'))

            if request.args.get("success"):
                return render_template('login.html', success = request.args.get('success'))

            if request.method == "POST" and request.form.get("action"):
                username = request.form.get("username")
                password = request.form.get("passwordInput") 
                action = request.form.get("action")
                #check for empty username or password
                if password in [None, ''] or username in [None, '']:
                    return render_template("login.html", error = 'EMPTY NAME OF PASSWORD')

                if action == "register":
                    #check if the username is already taken or not
                    with self.app.app_context():
                        stmt = select(self.Users).where(self.Users.username == username)
                        row = self.db.session.execute(stmt).scalars().all()

                    #the username is taken
                    if len(row) != 0:
                        return render_template('login.html', error = f"NAME '{username}' IS ALREADY TAKEN")

                    #insert the new user in the database
                    user_id = self.generate_unique_code(10, 'user_id')
                    with self.app.app_context():
                        self.db.session.add(self.Users(user_id=user_id, username=username, password=self.ph.hash(password),channel_id=None,user_type="NORMAL"))#type:ignore
                        self.db.session.commit()

                    return render_template("login.html", username = username, success = "YOU HAVE REGISTERED SUCCESSFULLY, USE YOUR CREDENTIALS TO LOGIN")
                elif action == "login":
                    with self.app.app_context():
                        stmt = select(self.Users).where(self.Users.username == username)
                        users = self.db.session.execute(stmt).scalars().all()

                    #the user is present
                    if len(users) != 0 and self.verify_password(users[0].password, password):#type:ignore
                        #set the session varaibles
                        session['user_id'] = users[0].user_id
                        session['username'] = users[0].username
                        session['channel_id'] = users[0].channel_id
                        #if the channel_id is present then redirect to that channel 
                        if session['channel_id'] != None: 
                            return redirect(url_for("channel"))
                        # or redirect to the welcome screen to join or create a channel
                        return redirect(url_for("welcome_screen"))
                    #return the user for entering wrong password
                    return render_template('login.html', username=username, error = 'NAME OR PASSWORD IS INCORRECT')
                else:
                    print("Some abnormal submit action has got from login page:", action)
                    return render_template('login.html', username=username)
            #normal rendering of login page
            return render_template('login.html')


        @self.app.route('/uploads/<filename>')
        def uploaded_file(filename):
            return send_from_directory('uploads', filename)

        @self.app.route("/delete_all_messages")
        def delete_all_messages():
            if self.check_session():
                return redirect(url_for("login"))

            with self.app.app_context():
                stmt = select(self.Channels).where(self.Channels.channel_id == session['channel_id'])
                channels = self.db.session.execute(stmt).scalars().all()
            #check if the user_id is same as that of the owner_id of the channel he is present in or else ridrect to the channel page
            if len(channels) == 0 or channels[0].owner_id != session['user_id']:
                return redirect(url_for("channel"))

            with self.app.app_context():
                stmt = delete(self.Messages)
                self.db.session.execute(stmt)
                self.db.session.commit()

            self.socketio.emit("new_message", {"message_type": "all_message_delete"}, to=session['channel_id'])
            return redirect(url_for("channel"))


        @self.app.route("/delete_channel")
        def delete_channel():
            if not self.check_session() and "channel_id" in session and urlparse(request.referrer).path == "/channel":
                with self.app.app_context():
                    stmt = update(self.Users).where(self.Users.channel_id == session['channel_id']).values(channel_id = None, user_type = "NORMAL")
                    self.db.session.execute(stmt)
                    stmt = delete(self.Channels).where(self.Channels.channel_id == session['channel_id'])
                    self.db.session.execute(stmt)
                    stmt = delete(self.Messages).where(self.Messages.channel_id == session['channel_id'])
                    self.db.session.execute(stmt)
                    self.db.session.commit()

                self.socketio.emit("exit_all", {"channel_id": session['channel_id']}, to=session['channel_id'])
                session.pop('channel_id', None)
            return redirect(url_for('welcome_screen', info = "CHANNEL DELETED SUCCESSFULLY"))


        #change channel name, description, password
        @self.app.route("/update_channel", methods = ["GET", "POST"])
        def update_channel():
            if not self.check_session() and "channel_id" in session and request.form.get('action') == 'updateChannel' and urlparse(request.referrer).path == "/channel":
                channel_name = request.form.get('channelNameOfModal') 
                channel_password = request.form.get('passwordInput') 
                channel_description = request.form.get('channelDescription') 
                if channel_name not in ['', None] and channel_password not in ['', None]:
                    with self.app.app_context():
                        stmt = update(self.Channels).where(self.Channels.channel_id == session['channel_id']).values(channel_name = channel_name, password = self.ph.hash(channel_password), channel_description = channel_description)#type:ignore
                        self.db.session.execute(stmt)
                        self.db.session.commit()
                    self.socketio.emit("new_message", {"content": f"Channel details are updated by {session['username']} [ {session['user_id']} ]", "message_type": "broadcast"}, to=session['channel_id'])
            return redirect(url_for("channel"))

        @self.app.route("/update_user", methods = ["GET", "POST"])
        def update_user():
            if not self.check_session() and request.method == "POST" and request.form.get('action') == 'updateUser' and urlparse(request.referrer).path == "/":
                username = request.form.get('usernameOfModal') 
                user_password = request.form.get('passwordInput') 
                if username not in ['', None] and user_password not in ['', None]:
                    #check if the username is already taken
                    with self.app.app_context():
                        stmt = select(self.Users).where(self.Users.username == username)
                        users = self.db.session.execute(stmt).scalars().all()

                    if len(users) != 0 and session['username'] != username:
                        return redirect(url_for("welcome_screen", error = f"NAME '{username}' IS ALREADY TAKEN, TRY WITH A DIFFERENT NAME"))
                    with self.app.app_context():
                        stmt = update(self.Users).where(self.Users.user_id == session['user_id']).values(username = username, password = self.ph.hash(user_password))#type:ignore
                        self.db.session.execute(stmt)
                        self.db.session.commit()
                    if session['username'] != username:
                        session['username'] = username

            return redirect(url_for('welcome_screen', success="USER PROFILE UPDATED SUCCESSFULLY"))

        @self.app.route("/leave_channel")
        def leave_channel():
            if not self.check_session() and "channel_id" in session and urlparse(request.referrer).path == "/channel":
                with self.app.app_context():
                    stmt = update(self.Users).where(self.Users.user_id == session['user_id'], self.Users.username == session['username']).values(channel_id = None, user_type = "NORMAL")
                    self.db.session.execute(stmt)
                    self.db.session.commit()

                self.socketio.emit("new_message", {"message_type": "broadcast", "content": f"{session['username']} [ {session['user_id']} ] has left the channel"}, to=session['channel_id'])
                session.pop('channel_id', None)

            return redirect(url_for('login'))



        @self.app.route("/delete_user")
        def delete_user():
            if not self.check_session() and urlparse(request.referrer).path == "/":
                #delete the user from the database and remove the session variables and redirect to login page
                with self.app.app_context():
                    stmt = select(self.Users).where(self.Users.username == session['username'], self.Users.user_id == session['user_id'])
                    users = self.db.session.execute(stmt).scalars().all()

                if len(users) != 0:
                    with self.app.app_context():
                        stmt = select(self.Channels).where(self.Channels.owner_id == session['user_id'])
                        channels = self.db.session.execute(stmt).scalars().all()

                    with self.app.app_context():
                        stmt = delete(self.Users).where(self.Users.user_id == session['user_id'], self.Users.username == session['username'])
                        self.db.session.execute(stmt)
                        self.db.session.commit()
                else:
                    return redirect(url_for("welcome_screen", error = f"INVALID USER, LOGIN REQUIRED"))

                for channel in channels:
                    self.socketio.emit("exit_all", None, to=channel.channel_id)

                    with self.app.app_context():
                        stmt = update(self.Users).where(self.Users.channel_id == channel.channel_id).values(channel_id = None)
                        self.db.session.execute(stmt)
                        stmt = delete(self.Channels).where(self.Channels.channel_id == channel.channel_id)
                        self.db.session.execute(stmt)
                        stmt = delete(self.Messages).where(self.Messages.channel_id == channel.channel_id)
                        self.db.session.execute(stmt)
                        self.db.session.commit()

                session.pop('user_id', None)
                session.pop('username', None)
                session.pop('channel_id', None)
                return redirect(url_for("login", success="USER DELETED SUCCESSFULLY"))
            return redirect(url_for('welcome_screen'))


        @self.app.route("/logout")
        def logout():
            if not self.check_session() and urlparse(request.referrer).path == "/":
                if 'channel_id' in session:
                    with self.app.app_context():
                        stmt = update(self.Users).where(self.Users.user_id == session['user_id'], self.Users.username == session['username']).values(channel_id = None, user_type = "NORMAL")
                        self.db.session.execute(stmt)
                        self.db.session.commit()

                session.pop('channel_id', None)
                session.pop('user_id', None)
                session.pop('username', None)

            return redirect(url_for('login'))


        #the welcome screen shows the welcome screen and takes in the name and sets it as the username
        #but if the username is already set then redirect the user to home screen
        @self.app.route("/", methods = ["GET", "POST"])
        def welcome_screen():
            if self.check_session():
                return redirect(url_for("login"))

            if request.method == "POST":

                #check if the post action was of join or create and redirect accordingly
                type_of_action = request.form.get("action")
                match type_of_action:
                    case "join":
                        return redirect(url_for("join"))
                    case "create":
                        return redirect(url_for("create"))
                    case '_':
                        print("An unknown post request is got via welcome post")
            if request.args.get("error"):
                return render_template('welcome.html', username=session['username'], error = request.args.get('error'))
            if request.args.get("info"):
                return render_template('welcome.html', username=session['username'], info = request.args.get('info'))
            if request.args.get("success"):
                return render_template('welcome.html', username=session['username'], success = request.args.get('success'))
            return render_template('welcome.html', username=session['username'])

        @self.app.route("/channel", methods=['GET', 'POST'])
        def channel():
            if self.check_session():
                return redirect(url_for("login"))

            if request.method == "GET":
                with self.app.app_context():
                    stmt = select(self.Channels).where(self.Channels.channel_id == session['channel_id'])
                    channels = self.db.session.execute(stmt).scalars().all()

                if len(channels) == 0:
                    print(f"No such channel: {session['channel_id']}")
                    return redirect(url_for("welcome_screen"))

                with self.app.app_context():
                    stmt = select(self.Users.username, self.Messages.sender_id, self.Messages.content, self.Messages.timestamp, self.Messages.message_type).join(self.Users , self.Users.user_id == self.Messages.sender_id).where(self.Messages.channel_id == session['channel_id']).order_by(self.Messages.timestamp)
                    messages = self.db.session.execute(stmt).all()
                return render_template("channel.html", code=session['channel_id'], messages=messages,owner_id=channels[0].owner_id, channel_name=channels[0].channel_name, channel_description=channels[0].channel_description, user_id=session['user_id'])
            elif request.method == "POST":
                with self.app.app_context():
                    stmt = select(self.Channels).where(self.Channels.channel_id == session['channel_id'])
                    channels = self.db.session.execute(stmt).scalars().all()
                if len(channels) == 0:
                    session.pop("channel_id")
                    return redirect(url_for("welcome_screen"))

                with self.app.app_context():
                    stmt = select(self.Users.username, self.Messages.sender_id, self.Messages.content, self.Messages.timestamp, self.Messages.message_type).join(self.Messages, self.Users.user_id == self.Messages.sender_id).where(self.Messages.channel_id == session['channel_id']).order_by(self.Messages.timestamp)
                    messages = self.db.session.execute(stmt).all()

                fileThing = request.files['fileThing']
                if not fileThing:
                    print("An error occured; file not sent")
                    return render_template("channel.html", code=session['channel_id'], messages=messages,owner_id=channels[0].owner_id, channel_name=channels[0].channel_name, channel_description=channels[0].channel_description, user_id=session['user_id'], error="NO FILE PROVIDED")
                path = pth.join(self.UPLOAD_FOLDER, fileThing.filename)#type: ignore
                fileThing.save(path)
                file_type = fileThing.mimetype
                #the below line checks for the file type
                message_type = 'i' if file_type.startswith('image/') else 'v' if file_type.startswith('video/') else 'a' if file_type.startswith('audio/') else 'f'
                print("*****","The type of file is: ", message_type,"*****")
                with self.app.app_context():
                    self.db.session.add(self.Messages(sender_id=session['user_id'], channel_id=session['channel_id'], content=path,timestamp=datetime.now().replace(microsecond = 0),message_type=message_type))#type:ignore
                    self.db.session.commit()

                self.socketio.emit("new_message", {"message_type": message_type, "content": path, "user_id": session['user_id'], "username" : session['username'], "timestamp": f"{datetime.now().replace(microsecond = 0)}"}, to=session['channel_id'])

                return render_template("channel.html", code=session['channel_id'], messages=messages,owner_id=channels[0].owner_id, channel_name=channels[0].channel_name, channel_description=channels[0].channel_description, user_id=session['user_id'])

            else:
                return redirect(url_for('login'))

        @self.app.route('/manage_users')
        def admin_panel():
            if self.check_session():
                return redirect(url_for("login"))

            with self.app.app_context():
                stmt = select(self.Channels).where(self.Channels.channel_id == session['channel_id'])
                channels = self.db.session.execute(stmt).scalars().all()
            #check if the user_id is same as that of the owner_id of the channel he is present in or else ridrect to the channel page
            if len(channels) == 0 or channels[0].owner_id != session['user_id']:
                return redirect(url_for("channel"))

        #TODO: allow and make available all the functins of the admin page
            

            return render_template("manage_page.html")


        @self.app.route('/join', methods = ['GET', 'POST'])
        def join():
            if self.check_session():
                return redirect(url_for("login"))

            with self.app.app_context():
                stmt = select(self.Users).where(self.Users.user_id == session['user_id'])
                users = self.db.session.execute(stmt).scalars().all()

            #if user already has a channel id
            if users[0].channel_id != None:
                session['channel_id'] = users[0].channel_id
                return redirect(url_for('channel'))

            if request.args.get("success"):
                return render_template("join_channel.html", username=session["username"], success = request.args.get('success'))

            if request.method == "POST":
                channel_id = request.form.get("channel-ID", False)
                #if channel_id is not null
                if channel_id:
                    with self.app.app_context():
                        stmt = select(self.Channels).where(self.Channels.channel_id == channel_id)
                        channels = self.db.session.execute(stmt).scalars().all()

                    #check if the channel is present or not and check password
                    if len(channels) != 1 or not self.verify_password(channels[0].password,request.form.get("passwordInput")): return render_template("join_channel.html", error=f"NO SUCH CHANNEL, OR PASSWORD IS INCORRECT", channel_id = channel_id, username=session['username']) #type:ignore
                    #see if the user is owner of the channel then update it's user_type and channel_id
                    user_type = 'OWNER' if channels[0].owner_id == session['user_id'] else 'NORMAL'

                    with self.app.app_context():
                        stmt = update(self.Users).where(self.Users.user_id == session['user_id']).values(user_type = user_type, channel_id = channel_id)
                        self.db.session.execute(stmt)
                        self.db.session.commit()

                    #set the channel_id for the user and redirect to channel page
                    session["channel_id"] = channel_id
                    self.socketio.emit("new_message", {"message_type": "broadcast", "content": f"{session['username']} [ {session['user_id']} ] has joined the channel"}, to=session['channel_id'])
                    return redirect(url_for("channel"))
            return render_template("join_channel.html", username=session["username"])

        @self.app.route('/create', methods = ['GET', 'POST'])
        def create():
            if self.check_session():
                return redirect(url_for("login"))

            with self.app.app_context():
                stmt = select(self.Users).where(self.Users.user_id == session['user_id'])
                channels = self.db.session.execute(stmt).scalars().all()

            #if user already has a channel id
            if channels[0].channel_id != None:
                session['channel_id'] = channels[0].channel_id
                return redirect(url_for('channel'))

            if request.method == "POST":
                action = request.form.get("action", False)
                if action == "create":
                    #get channel id, channel name, password and description as provided
                    channel_id = self.generate_unique_code(5, "channel_id");
                    channel_password = request.form.get("passwordInput")
                    channel_name = request.form.get("channel-name")
                    channel_description = request.form.get("channel-description")

                    #return to the page with an error if there is no password provided
                    if request.form.get("passwordInput") in ["",None]:
                        return render_template("create_channel.html", error="PASSWORD IS NOT PROVIDED")

                    with self.app.app_context():
                        self.db.session.add(self.Channels(channel_id=channel_id, channel_name=channel_name, channel_description=channel_description,password=self.ph.hash(channel_password),owner_id=session['user_id']))#type:ignore
                        self.db.session.commit()
                    return redirect(url_for('join', success = f"CHANNEL ID OF NEWLY CREATED CHANNEL IS: '{channel_id}' - SAVE THIS CODE TO ACCESS THE CHANNEL"))

            return render_template("create_channel.html", username=session["username"])


        @self.socketio.on('send_message')
        def message(data):
            channel_id = session['channel_id'] 
            username = session['username']

            current_time = datetime.now().replace(microsecond = 0)
            content = {
                "username": username,
                "user_id": session['user_id'],
                "content": data["data"],
                "timestamp" : current_time.strftime("%Y-%m-%d %H:%M:%S")
            }
            self.socketio.emit("new_message", content, to=channel_id)
            with self.app.app_context():
                self.db.session.add(self.Messages(sender_id=session['user_id'],channel_id=channel_id, content=data['data'], timestamp=current_time))#type:ignore
                self.db.session.commit()

            print(f'{username} said: {data["data"]}')

        @self.socketio.on('connect')
        def connect(auth):
            channel_id = session["channel_id"]
            username = session["username"]
            user_id = session["user_id"]
            if not channel_id or not username or not user_id:
                return
            with self.app.app_context():
                stmt = select(self.Channels).where(self.Channels.channel_id == channel_id)
                channels = self.db.session.execute(stmt).scalars().all()

            if len(channels) != 1:
                leave_room(channel_id)
                return
            join_room(channel_id)


        @self.socketio.on("disconnect")
        def disconnect():
            channel_id = session['channel_id']
            leave_room(channel_id)

if __name__ == '__main__':
    app = ChatApp()
    app.run()
