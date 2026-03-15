from supabase import create_client
from config import SUPABASE_URL,SUPABASE_KEY

supabase=create_client(SUPABASE_URL,SUPABASE_KEY)

def get_user(uid):
    r=supabase.table("users").select("*").eq("id",uid).execute()
    return r.data[0] if r.data else None

def create_user(uid,username,ref):
    supabase.table("users").insert({
        "id":uid,
        "username":username,
        "referred_by":ref
    }).execute()

def add_points(uid,n):
    u=get_user(uid)
    supabase.table("users").update({
        "points":u["points"]+n
    }).eq("id",uid).execute()
