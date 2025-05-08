import logging
from supabase import create_client, Client
from datetime import datetime, timezone
import config

logger = logging.getLogger(__name__)

supabase_client: Client | None = None
CHAT_HISTORY_TABLE = "chat_history" # Nama tabel di Supabase

def init_supabase_client():
    """Menginisialisasi klien Supabase."""
    global supabase_client
    if config.SUPABASE_URL and config.SUPABASE_KEY:
        try:
            supabase_client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
            logger.info("Klien Supabase berhasil diinisialisasi.")
        except Exception as e:
            logger.error(f"Gagal menginisialisasi klien Supabase: {e}")
            supabase_client = None
    else:
        logger.warning("URL atau Kunci Supabase tidak ada di konfigurasi. Fitur Supabase akan dinonaktifkan.")
        supabase_client = None

def add_message_to_history(chat_id: int, role: str, content: str) -> bool:
    """Menambahkan pesan ke tabel riwayat chat di Supabase."""
    if not supabase_client:
        logger.warning("Supabase client tidak tersedia. Pesan tidak bisa ditambahkan ke riwayat.")
        return False
    try:
        timestamp = datetime.now(timezone.utc).isoformat()

        response = supabase_client.table(CHAT_HISTORY_TABLE).insert({
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "message_timestamp": timestamp
        }).execute()

        if hasattr(response, 'data') and response.data:
             logger.debug(f"Pesan untuk chat_id {chat_id} berhasil ditambahkan ke riwayat Supabase.")
             return True
        elif hasattr(response, 'error') and response.error:
             logger.error(f"Error Supabase saat menambahkan pesan untuk chat_id {chat_id}: {response.error.message}")
             return False
        else:
             logger.warning(f"Respons tidak dikenali dari Supabase saat menambahkan pesan untuk chat_id {chat_id}. Mungkin berhasil.")

             return True # Atau False jika ingin lebih ketat

    except Exception as e:
        logger.error(f"Pengecualian saat menambahkan pesan ke Supabase untuk chat_id {chat_id}: {e}")
        return False

def get_chat_history(chat_id: int) -> list:
    """Mengambil riwayat percakapan dari Supabase untuk chat_id tertentu."""
    if not supabase_client:
        logger.warning("Supabase client tidak tersedia. Tidak bisa mengambil riwayat chat.")
        return []
    try:
        response = supabase_client.table(CHAT_HISTORY_TABLE)\
            .select("role, content")\
            .eq("chat_id", chat_id)\
            .order("message_timestamp", desc=True)\
            .limit(config.CHAT_HISTORY_MESSAGES_LIMIT)\
            .execute()

        formatted_history = []
        if response.data:
            for item in reversed(response.data):

                formatted_history.append({"role": item["role"], "parts": [{"text": item["content"]}]})
            logger.debug(f"Mengambil {len(formatted_history)} pesan dari riwayat Supabase untuk chat_id {chat_id}.")
        return formatted_history
    except Exception as e:
        logger.error(f"Error mengambil riwayat chat dari Supabase untuk chat_id {chat_id}: {e}")
        return []

def delete_chat_history_db(chat_id: int) -> bool:
    """Menghapus semua riwayat percakapan untuk chat_id tertentu dari Supabase."""
    if not supabase_client:
        logger.warning("Supabase client tidak tersedia. Tidak bisa menghapus riwayat chat.")
        return False
    try:
        response = supabase_client.table(CHAT_HISTORY_TABLE).delete().eq("chat_id", chat_id).execute()

        if hasattr(response, 'data') and response.data is not None: # response.data bisa berupa list (kosong atau berisi)
             logger.info(f"Riwayat chat untuk chat_id {chat_id} berhasil dihapus dari Supabase.")
             return True
        elif hasattr(response, 'error') and response.error:
             logger.error(f"Error Supabase saat menghapus riwayat untuk chat_id {chat_id}: {response.error.message}")
             return False
        else:
             logger.warning(f"Respons tidak dikenali dari Supabase saat menghapus riwayat untuk chat_id {chat_id}. Mungkin berhasil.")
             return True # Atau False jika ingin lebih ketat

    except Exception as e:
        logger.error(f"Pengecualian saat menghapus riwayat chat dari Supabase untuk chat_id {chat_id}: {e}")
        return False


init_supabase_client()
