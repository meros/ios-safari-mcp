/*
 * wi_relay: stands in for webinspectord, and fronts the Web Inspector with TCP.
 *
 * Safari's processes (the app, and every WebContent process) look up the mach service "com.apple.webinspector" and send
 * it XPC dictionaries {"msgData": {"<UUID>": <data>}}; the data is a property list (format "bplist15") with the
 * keys messageName and userInfo. The real daemon (webinspectord) did not answer a client that came through
 * guest/wi_bridge, and its code is in the dyld cache, so this program takes the service name itself (launchd job
 * com.redwood.wirelay; the job of webinspectord is left out of the image). The applications only connect after the
 * daemon's Darwin notification com.apple.webinspectord.available, which this program posts, and again on every
 * com.apple.webinspectord.availability_check.
 *
 * Control channel: a frame for peer 0xFFFFFFFF is a command for this program, not for a process. "open-url URL" asks
 * SpringBoard to open the URL (SBSOpenSensitiveURLAndUnlock, as the parent repo's battery_test does; it needs the
 * entitlement com.apple.springboard.opensensitiveurl, see guest/wi_relay.entitlements), which starts Safari if it is not
 * running and works when no web page exists. The answer comes back as a frame from peer 0xFFFFFFFF: "ok ..." or "error ...".
 *
 * TCP side: one client at a time, frames of  u32 length (big endian) | u32 peer | data  where length counts
 * peer and data. From the guest the data is what a peer sent (peer = its number, from 1); a frame with no data says
 * that the peer is gone. To the guest, peer 0 means every peer; the data goes out as {"msgData": {"<UUID>": data}}
 * with the UUID that peer used (the last one it sent; before it has sent anything, CoreFoundation's constant).
 *
 * usage: wi_relay [PORT]    (default 27753; started by launchd)
 * Built without the iOS SDK (see ../build-guest.sh).
 */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef void *xpc_object_t;
typedef void *xpc_connection_t;
typedef void *dispatch_queue_t;
typedef void *xpc_type_t;
typedef void (^xpc_handler_t)(xpc_object_t);

extern const struct _xpc_type_s _xpc_type_connection;
extern const struct _xpc_type_s _xpc_type_dictionary;
extern const struct _xpc_type_s _xpc_type_error;
extern const struct _xpc_type_s _xpc_type_data;
#define T_CONNECTION ((xpc_type_t)&_xpc_type_connection)
#define T_DICTIONARY ((xpc_type_t)&_xpc_type_dictionary)
#define T_ERROR ((xpc_type_t)&_xpc_type_error)
#define T_DATA ((xpc_type_t)&_xpc_type_data)

extern xpc_connection_t xpc_connection_create_mach_service(const char *name, dispatch_queue_t targetq, uint64_t flags);
extern void xpc_connection_set_event_handler(xpc_connection_t c, xpc_handler_t handler);
extern void xpc_connection_resume(xpc_connection_t c);
extern void xpc_connection_cancel(xpc_connection_t c);
extern void xpc_connection_send_message(xpc_connection_t c, xpc_object_t message);
extern xpc_type_t xpc_get_type(xpc_object_t o);
extern xpc_object_t xpc_dictionary_create(const char *const *keys, const xpc_object_t *values, size_t count);
extern const void *xpc_dictionary_get_data(xpc_object_t d, const char *key, size_t *length);
extern void xpc_dictionary_set_data(xpc_object_t d, const char *key, const void *bytes, size_t length);
extern char *xpc_copy_description(xpc_object_t o);
extern xpc_object_t xpc_retain(xpc_object_t o);
extern xpc_object_t xpc_dictionary_get_value(xpc_object_t d, const char *key);
extern void xpc_dictionary_set_value(xpc_object_t d, const char *key, xpc_object_t value);
extern bool xpc_dictionary_apply(xpc_object_t d, bool (^applier)(const char *key, xpc_object_t value));
extern size_t xpc_data_get_length(xpc_object_t data);
extern size_t xpc_data_get_bytes(xpc_object_t data, void *buffer, size_t off, size_t length);
extern void xpc_release(xpc_object_t o);

extern int socket(int, int, int);
extern int bind(int, const void *, unsigned);
extern int listen(int, int);
extern int accept(int, void *, unsigned *);
extern int setsockopt(int, int, int, const void *, unsigned);
extern long read(int, void *, unsigned long);
extern long write(int, const void *, unsigned long);
extern int close(int);
extern int shutdown(int, int);
extern int dprintf(int, const char *, ...);
extern void (*signal(int, void (*)(int)))(int);
extern int *__error(void);
extern void *memset(void *, int, unsigned long);
extern int atoi(const char *);
extern void free(void *);
extern void *memmem(const void *big, unsigned long big_len, const void *little, unsigned long little_len);
extern void *memcpy(void *, const void *, unsigned long);
extern void *dlopen(const char *path, int mode);
extern void *dlsym(void *handle, const char *symbol);
extern void dispatch_async(dispatch_queue_t queue, void (^block)(void));
extern int strncmp(const char *, const char *, unsigned long);
extern unsigned long strlen(const char *);
typedef const void *CFURLRef;
extern CFURLRef CFURLCreateWithBytes(const void *allocator, const uint8_t *bytes, long length, uint32_t encoding, CFURLRef base);
extern void CFRelease(const void *object);
extern void *malloc(unsigned long);
extern int pthread_mutex_lock(void *);
extern int pthread_mutex_unlock(void *);
extern int pthread_mutex_init(void *, const void *);
extern uint32_t notify_post(const char *name);
extern uint32_t notify_register_dispatch(const char *name, int *out_token, dispatch_queue_t queue, void (^handler)(int token));
extern dispatch_queue_t dispatch_get_global_queue(long identifier, unsigned long flags);

struct pollfd_ {
	int fd;
	short events, revents;
};
extern int poll(void *, unsigned, int);

struct timeval_ {          /* Darwin's struct timeval */
	long sec;
	int usec;
	int pad;
};

struct sockaddr_in_ {
	uint8_t len, family;
	uint16_t port;
	uint32_t addr;
	char zero[8];
};

static void strncpy_(char *dst, const char *src, unsigned long n)
{
	unsigned long i = 0;
	for (; i < n && src[i]; i++) {
		dst[i] = src[i];
	}
	dst[i] = 0;
}

#define LOG(...) dprintf(1, "wi_relay: " __VA_ARGS__)
#define MAX_PEERS 64
#define CONTROL_PEER 0xFFFFFFFFu
#define UTF8_ENCODING 0x08000100u   /* kCFStringEncodingUTF8 */
#define SEND_TIMEOUT_S 5            /* a host that stops reading is dropped after this long */
#define MAX_OPEN_URL 4              /* open-url commands running at once */
#define MAX_MESSAGE (1u << 20)      /* a larger message from a process is dropped (and logged) */
/* the key CoreFoundation's XPC encoding gives to a data value; the same in every process that was captured */
#define CF_XPC_KEY "ECF19A18-7AA6-4141-B4DC-A2E5123B2B5C"

/* pthread_mutex_t is 64 bytes on arm64 Darwin */
static char lock[64] __attribute__((aligned(8)));
struct peer {
	xpc_connection_t conn;
	uint32_t id;
	char key[64];
	void *listing;     /* the last WIRListingMessage of this peer, replayed to a client that connects later */
	size_t listing_len;
};
static struct peer peers[MAX_PEERS];
static int npeers;
static uint32_t next_peer = 1;
static char default_key[64] = CF_XPC_KEY;
static int client_fd = -1;                 /* guarded by `lock`: many threads read it, only the main thread closes it */
static volatile int open_url_running;

static int write_all(int fd, const uint8_t *p, size_t n)
{
	while (n > 0) {
		long w = write(fd, p, n);
		if (w < 0 && *__error() == 4 /* EINTR */) {
			continue;
		}
		if (w <= 0) {
			return 0;          /* an error, or the send timeout */
		}
		p += w;
		n -= (size_t)w;
	}
	return 1;
}

/* One frame to the host, written in one piece under the lock: frames of different threads never interleave, and the
 * client cannot be closed and its descriptor reused while a write is going on. A failed or timed out write ends the
 * connection (shutdown wakes the main loop, which closes it): a half written frame must never be followed by another. */
static void to_client(uint32_t peer, const void *data, size_t len)
{
	size_t total = len + 4;
	uint8_t *frame = malloc(len + 8);
	if (!frame) {
		return;
	}
	frame[0] = (uint8_t)(total >> 24);
	frame[1] = (uint8_t)(total >> 16);
	frame[2] = (uint8_t)(total >> 8);
	frame[3] = (uint8_t)total;
	frame[4] = (uint8_t)(peer >> 24);
	frame[5] = (uint8_t)(peer >> 16);
	frame[6] = (uint8_t)(peer >> 8);
	frame[7] = (uint8_t)peer;
	if (len) {
		memcpy(frame + 8, data, len);
	}
	pthread_mutex_lock(&lock);
	if (client_fd >= 0 && !write_all(client_fd, frame, len + 8)) {
		LOG("write to the client failed, dropping it\n");
		shutdown(client_fd, 2 /* SHUT_RDWR */);
	}
	pthread_mutex_unlock(&lock);
	free(frame);
}

/* the main thread only: swap or end the client under the lock */
static void set_client(int fd)
{
	pthread_mutex_lock(&lock);
	if (client_fd >= 0) {
		close(client_fd);
	}
	client_fd = fd;
	pthread_mutex_unlock(&lock);
}

static int peer_id_locked(xpc_connection_t conn)
{
	for (int i = 0; i < npeers; i++) {
		if (peers[i].conn == conn) {
			return (int)peers[i].id;
		}
	}
	return 0;
}

static int peer_id(xpc_connection_t conn)
{
	pthread_mutex_lock(&lock);
	int id = peer_id_locked(conn);
	pthread_mutex_unlock(&lock);
	return id;
}

static void peer_gone(xpc_connection_t peer)
{
	pthread_mutex_lock(&lock);
	int id = peer_id_locked(peer);
	for (int i = 0; i < npeers; i++) {
		if (peers[i].conn == peer) {
			xpc_release(peers[i].conn);
			free(peers[i].listing);
			peers[i] = peers[--npeers];
			break;
		}
	}
	pthread_mutex_unlock(&lock);
	if (id) {
		LOG("peer %d gone\n", id);
		to_client((uint32_t)id, NULL, 0);
	}
}

static void peer_message(xpc_connection_t peer, xpc_object_t msg)
{
	if (xpc_get_type(msg) == T_ERROR) {
		/* the connection of a process ended (invalid): the peer is gone */
		char *d = xpc_copy_description(msg);
		LOG("peer %d: %.160s\n", peer_id(peer), d ? d : "?");
		free(d);
		peer_gone(peer);
		return;
	}
	if (xpc_get_type(msg) != T_DICTIONARY || !xpc_dictionary_get_value(msg, "msgData")) {
		return;
	}
	/* {"msgData": {"<connection UUID>": <data>}}: the data goes to the TCP client */
	xpc_object_t inner = xpc_dictionary_get_value(msg, "msgData");
	if (xpc_get_type(inner) != T_DICTIONARY) {
		LOG("peer %d: msgData is not a dictionary\n", peer_id(peer));
		return;
	}
	int id = peer_id(peer);
	xpc_dictionary_apply(inner, ^bool(const char *key, xpc_object_t value) {
		if (xpc_get_type(value) != T_DATA) {
			LOG("peer %d: a value of msgData is not data, ignored\n", id);
			return true;
		}
		size_t len = xpc_data_get_length(value);
		pthread_mutex_lock(&lock);
		for (int i = 0; i < npeers; i++) {
			if (peers[i].id == (uint32_t)id) {
				strncpy_(peers[i].key, key, sizeof(peers[i].key) - 1);
			}
		}
		strncpy_(default_key, key, sizeof(default_key) - 1);
		pthread_mutex_unlock(&lock);
		if (len >= MAX_MESSAGE) {
			LOG("peer %d: a message of %zu bytes is dropped\n", id, len);
		}
		if (len > 0 && len < MAX_MESSAGE) {
			uint8_t *buf = malloc(len);
			if (buf) {
				xpc_data_get_bytes(value, buf, 0, len);
				if (memmem(buf, len, "WIRListingMessage", 17)) {
					void *copy = malloc(len);
					if (copy) {
						memcpy(copy, buf, len);
						pthread_mutex_lock(&lock);
						for (int i = 0; i < npeers; i++) {
							if (peers[i].id == (uint32_t)id) {
								free(peers[i].listing);
								peers[i].listing = copy;
								peers[i].listing_len = len;
								copy = NULL;
							}
						}
						pthread_mutex_unlock(&lock);
						free(copy);
					}
				}
				to_client((uint32_t)id, buf, len);
				free(buf);
			}
		}
		return true;
	});
}

static void new_peer(xpc_connection_t peer)
{
	pthread_mutex_lock(&lock);
	int id = 0;
	if (npeers < MAX_PEERS) {
		struct peer *p = &peers[npeers++];
		memset(p, 0, sizeof(*p));
		p->conn = (xpc_connection_t)xpc_retain(peer);
		p->id = id = (int)next_peer++;
	}
	pthread_mutex_unlock(&lock);
	if (!id) {
		LOG("more than %d processes: a connection is refused\n", MAX_PEERS);
		xpc_connection_set_event_handler(peer, ^(xpc_object_t msg) {
			(void)msg;
		});
		xpc_connection_resume(peer);
		xpc_connection_cancel(peer);
		return;
	}
	LOG("peer %d connected\n", id);
	xpc_connection_set_event_handler(peer, ^(xpc_object_t msg) {
		peer_message(peer, msg);
	});
	xpc_connection_resume(peer);
}

static void to_peers(uint32_t target, const void *data, size_t len)
{
	pthread_mutex_lock(&lock);
	for (int i = 0; i < npeers; i++) {
		if (target && peers[i].id != target) {
			continue;
		}
		xpc_object_t inner = xpc_dictionary_create(NULL, NULL, 0);
		xpc_dictionary_set_data(inner, peers[i].key[0] ? peers[i].key : default_key, data, len);
		xpc_object_t m = xpc_dictionary_create(NULL, NULL, 0);
		xpc_dictionary_set_value(m, "msgData", inner);
		xpc_connection_send_message(peers[i].conn, m);
		xpc_release(m);
		xpc_release(inner);
	}
	pthread_mutex_unlock(&lock);
}


static void reply(const char *text)
{
	to_client(CONTROL_PEER, text, strlen(text));
}

/* "open-url URL": SpringBoard opens it in the default application (Safari for http and https) */
static void open_url(const char *text)
{
	if (strncmp(text, "http://", 7) != 0 && strncmp(text, "https://", 8) != 0 && strncmp(text, "about:", 6) != 0) {
		reply("error only http, https and about: addresses");
		return;
	}
	for (const char *c = text; *c; c++) {
		if ((uint8_t)*c < 0x20 || *c == 0x7f) {
			reply("error a control character in the address");
			return;
		}
	}
	void *sbs = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/SpringBoardServices", 2 /* RTLD_NOW */);
	/* the function returns a BOOL, which is 8 bits: only the low byte of the result means anything */
	uint8_t (*open)(CFURLRef, uint8_t) = sbs ? (uint8_t (*)(CFURLRef, uint8_t))dlsym(sbs, "SBSOpenSensitiveURLAndUnlock") : NULL;
	if (!open) {
		reply("error SBSOpenSensitiveURLAndUnlock is not available");
		return;
	}
	CFURLRef url = CFURLCreateWithBytes(NULL, (const uint8_t *)text, (long)strlen(text), UTF8_ENCODING, NULL);
	if (!url) {
		reply("error not a URL");
		return;
	}
	uint8_t result = open(url, 1);
	CFRelease(url);
	LOG("SpringBoard was asked to open %s: %s\n", text, result ? "it does" : "it refuses");
	reply(result ? "ok SpringBoard opens the address" : "error SpringBoard refuses (is the entitlement in the signature?)");
}

static void control(const char *data, size_t len)
{
	char *cmd = malloc(len + 1);
	if (!cmd) {
		return;
	}
	memcpy(cmd, data, len);
	cmd[len] = 0;
	if (strncmp(cmd, "open-url ", 9) != 0) {
		reply("error unknown command");
		free(cmd);
		return;
	}
	/* SpringBoard can take long, and each command holds a worker thread: a few at a time */
	if (__sync_add_and_fetch(&open_url_running, 1) > MAX_OPEN_URL) {
		__sync_sub_and_fetch(&open_url_running, 1);
		reply("error busy: wait for the running open-url commands");
		free(cmd);
		return;
	}
	dispatch_async(dispatch_get_global_queue(0, 0), ^{
		open_url(cmd + 9);
		__sync_sub_and_fetch(&open_url_running, 1);
		free(cmd);
	});
}

/* what each process listed last, so that a client need not wait for a page to change */
static void replay_listings(void)
{
	pthread_mutex_lock(&lock);
	for (int i = 0; i < npeers; i++) {
		if (peers[i].listing) {
			void *copy = malloc(peers[i].listing_len);
			if (copy) {
				memcpy(copy, peers[i].listing, peers[i].listing_len);
				uint32_t pid = peers[i].id;
				size_t plen = peers[i].listing_len;
				pthread_mutex_unlock(&lock);
				to_client(pid, copy, plen);
				pthread_mutex_lock(&lock);
				free(copy);
			}
		}
	}
	pthread_mutex_unlock(&lock);
}

int main(int argc, char **argv)
{
	int port = argc > 1 ? atoi(argv[1]) : 27753;
	signal(13 /* SIGPIPE */, (void (*)(int))1 /* SIG_IGN */);
	pthread_mutex_init(lock, NULL);

	xpc_connection_t svc = xpc_connection_create_mach_service("com.apple.webinspector", NULL, 1 /* LISTENER */);
	if (!svc) {
		LOG("cannot take the mach service\n");
		return 1;
	}
	xpc_connection_set_event_handler(svc, ^(xpc_object_t event) {
		if (xpc_get_type(event) == T_CONNECTION) {
			new_peer((xpc_connection_t)event);
		} else {
			char *d = xpc_copy_description(event);
			LOG("service event: %.300s\n", d ? d : "?");
			free(d);
		}
	});
	xpc_connection_resume(svc);
	LOG("holding com.apple.webinspector\n");

	/* what webinspectord does so that applications connect: say that the service is there, and say it again
	 * whenever an application asks (WebKit posts the availability check when it starts), and say that
	 * inspection is on */
	static int token;
	notify_register_dispatch("com.apple.webinspectord.availability_check", &token, dispatch_get_global_queue(0, 0),
	    ^(int t) {
		(void)t;
		LOG("availability check: answering\n");
		notify_post("com.apple.webinspectord.available");
		notify_post("com.apple.webinspectord.enabled");
	});
	notify_post("com.apple.webinspectord.available");
	notify_post("com.apple.webinspectord.enabled");

	int ls = socket(2 /* AF_INET */, 1 /* SOCK_STREAM */, 0);
	int one = 1;
	setsockopt(ls, 0xffff /* SOL_SOCKET */, 4 /* SO_REUSEADDR */, &one, sizeof(one));
	struct sockaddr_in_ sa;
	memset(&sa, 0, sizeof(sa));
	sa.len = sizeof(sa);
	sa.family = 2;
	sa.port = (uint16_t)(((port & 0xff) << 8) | ((port >> 8) & 0xff));
	if (bind(ls, &sa, sizeof(sa)) != 0 || listen(ls, 4) != 0) {
		LOG("bind/listen on %d failed, errno %d\n", port, *__error());
		return 1;
	}
	LOG("listening on port %d\n", port);
	/* One client at a time; a client that connects while another is attached replaces it (the old one is a host
	 * process that died without closing, or an earlier run). */
	uint8_t *buf = NULL;
	size_t have = 0, want = 4;            /* bytes of the frame header or body read so far, and wanted */
	int reading_body = 0;
	for (;;) {
		struct pollfd_ p[2] = { { ls, 1, 0 }, { client_fd, 1, 0 } };
		if (poll(p, client_fd >= 0 ? 2 : 1, -1) < 0) {
			continue;
		}
		if (p[0].revents & 1) {
			int fd = accept(ls, NULL, NULL);
			if (fd >= 0) {
				struct timeval_ tv = { SEND_TIMEOUT_S, 0, 0 };
				setsockopt(fd, 0xffff /* SOL_SOCKET */, 0x1005 /* SO_SNDTIMEO */, &tv, sizeof(tv));
				if (client_fd >= 0) {
					LOG("a new client replaces the old one\n");
				}
				LOG("client connected\n");
				free(buf);
				buf = NULL;
				have = 0;
				want = 4;
				reading_body = 0;
				set_client(fd);
				replay_listings();
			}
			continue;
		}
		if (client_fd >= 0 && (p[1].revents & (1 | 8 | 16))) {
			if (!buf) {
				buf = malloc(want < 4 ? 4 : want);
			}
			long r = read(client_fd, buf + have, want - have);
			if (r <= 0) {
				LOG("client done\n");
				set_client(-1);
				free(buf);
				buf = NULL;
				have = 0;
				want = 4;
				reading_body = 0;
				continue;
			}
			have += (size_t)r;
			if (have < want) {
				continue;
			}
			if (!reading_body) {
				size_t len = ((size_t)buf[0] << 24) | ((size_t)buf[1] << 16) | ((size_t)buf[2] << 8) | buf[3];
				free(buf);
				buf = NULL;
				if (len < 4 || len > (16u << 20)) {
					LOG("bad frame length %zu: the client is dropped\n", len);
					set_client(-1);
					have = 0;
					want = 4;
					continue;
				}
				want = len;
				have = 0;
				reading_body = 1;
				continue;
			}
			const uint8_t *f = buf;
			uint32_t target = ((uint32_t)f[0] << 24) | ((uint32_t)f[1] << 16) | ((uint32_t)f[2] << 8) | f[3];
			if (target == CONTROL_PEER) {
				control((const char *)f + 4, want - 4);
			} else {
				to_peers(target, f + 4, want - 4);
			}
			free(buf);
			buf = NULL;
			have = 0;
			want = 4;
			reading_body = 0;
		}
	}
}
