from sqlcipher3 import dbapi2 as sqlcipher

connect = sqlcipher.connect#type: ignore
apilevel = sqlcipher.apilevel
paramstyle = sqlcipher.paramstyle
threadsafety = sqlcipher.threadsafety
sqlite_version_info = sqlcipher.sqlite_version_info
version_info = sqlcipher.version_info 

Error = sqlcipher.DatabaseError#type:ignore
ProgrammingError = sqlcipher.ProgrammingError#type:ignore
IntegrityError = sqlcipher.IntegrityError#type:ignore
OperationalError = sqlcipher.OperationalError#type:ignore
