/*
 * wi_bridge: a TCP front for the guest's Web Inspector.
 *
 * lockdownd starts the lockdown service "com.apple.webinspector" by sending an XPC message to the mach service of
 * the same name (webinspectord, a thin shell around RWIRelay in WebInspector.framework). The message carries the
 * client's socket as the XPC file descriptor _LDSERVICESOCK, and the process on the other end then speaks
 * Web Inspector's own protocol on that socket: a 4 byte big endian length, then a binary property list.
 * This program sends the same message itself, for every client that connects to TCP port PORT (default 27753),
 * and copies bytes both ways. No lockdown, no pairing, no usbmuxd.
 *
 * usage: wi_bridge [PORT]
 *
 * Built without the iOS SDK: the few prototypes it needs are declared here (see ../build-guest.sh).
 * The keys of the message were read from lockdownd's spawn_xpc_service (arm64 disassembly, iOS 16.5); that the
 * receiving side accepts a message without lockdownd's other fields is what the first run has to show.
 */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef void *xpc_object_t;
typedef void *xpc_connection_t;
typedef void *dispatch_queue_t;
typedef void (^xpc_handler_t)(xpc_object_t);

extern xpc_connection_t xpc_connection_create_mach_service(const char *name, dispatch_queue_t targetq, uint64_t flags);
extern void xpc_connection_set_event_handler(xpc_connection_t c, xpc_handler_t handler);
extern void xpc_connection_resume(xpc_connection_t c);
extern void xpc_connection_send_message(xpc_connection_t c, xpc_object_t message);
extern xpc_object_t xpc_dictionary_create(const char *const *keys, const xpc_object_t *values, size_t count);
extern void xpc_dictionary_set_string(xpc_object_t d, const char *key, const char *value);
extern void xpc_dictionary_set_bool(xpc_object_t d, const char *key, bool value);
extern void xpc_dictionary_set_fd(xpc_object_t d, const char *key, int fd);
extern void xpc_dictionary_set_value(xpc_object_t d, const char *key, xpc_object_t value);
extern xpc_object_t xpc_date_create_from_current(void);
extern void xpc_dictionary_set_date(xpc_object_t d, const char *key, int64_t value);
extern char *xpc_copy_description(xpc_object_t o);

extern int socket(int, int, int);
extern int bind(int, const void *, unsigned);
extern int listen(int, int);
extern int accept(int, void *, unsigned *);
extern int setsockopt(int, int, int, const void *, unsigned);
extern int socketpair(int, int, int, int *);
extern long read(int, void *, unsigned long);
extern long write(int, const void *, unsigned long);
extern int close(int);
extern int poll(void *, unsigned, int);
extern int dprintf(int, const char *, ...);
extern void (*signal(int, void (*)(int)))(int);
extern int *__error(void);
extern void *memset(void *, int, unsigned long);
extern int atoi(const char *);
extern uint32_t notify_post(const char *name);
extern void free(void *);

struct sockaddr_in_ {
	uint8_t len, family;
	uint16_t port;
	uint32_t addr;
	char zero[8];
};
struct pollfd_ { int fd; short events, revents; };

#define LOG(...) dprintf(1, "wi_bridge: " __VA_ARGS__)

static void relay(int tcp, int svc)
{
	char buf[16384];
	for (;;) {
		struct pollfd_ p[2] = { { tcp, 1, 0 }, { svc, 1, 0 } };
		if (poll(p, 2, -1) < 0) {
			return;
		}
		for (int i = 0; i < 2; i++) {
			if (p[i].revents & 1) {
				long n = read(p[i].fd, buf, sizeof(buf));
				if (n <= 0) {
					return;
				}
				int to = i == 0 ? svc : tcp;
				for (long off = 0; off < n;) {
					long w = write(to, buf + off, (unsigned long)(n - off));
					if (w <= 0) {
						return;
					}
					off += w;
				}
			} else if (p[i].revents & (8 | 16)) {
				return;
			}
		}
	}
}

static void start_service(int *service_end, int *our_end, int *ok)
{
	int sv[2];
	*ok = 0;
	if (socketpair(1 /* AF_UNIX */, 1 /* SOCK_STREAM */, 0, sv) != 0) {
		LOG("socketpair failed, errno %d\n", *__error());
		return;
	}
	xpc_connection_t conn = xpc_connection_create_mach_service("com.apple.webinspector", NULL, 0);
	if (!conn) {
		LOG("xpc_connection_create_mach_service returned NULL\n");
		close(sv[0]);
		close(sv[1]);
		return;
	}
	xpc_connection_set_event_handler(conn, ^(xpc_object_t event) {
		char *d = xpc_copy_description(event);
		LOG("xpc event: %s\n", d ? d : "?");
		free(d);
	});
	xpc_connection_resume(conn);

	xpc_object_t info = xpc_dictionary_create(NULL, NULL, 0);
	xpc_dictionary_set_string(info, "Label", "com.apple.webinspector");
	xpc_dictionary_set_string(info, "XPCServiceName", "com.apple.webinspector");
	xpc_dictionary_set_string(info, "UserName", "mobile");

	xpc_object_t msg = xpc_dictionary_create(NULL, NULL, 0);
	xpc_dictionary_set_string(msg, "_LDCHECKININFO", "xpc");
	xpc_dictionary_set_value(msg, "_LDCHECKINDICT", info);
	xpc_dictionary_set_fd(msg, "_LDSERVICESOCK", sv[1]);
	xpc_dictionary_set_bool(msg, "_LDSHUTDOWNLISTENER", false);
	xpc_dictionary_set_bool(msg, "_LDKEEPCONNALIVE", true);
	xpc_object_t now = xpc_date_create_from_current();
	xpc_dictionary_set_value(msg, "_LDTIMESTAMP", now);
	xpc_connection_send_message(conn, msg);
	close(sv[1]);
	*service_end = sv[1];
	*our_end = sv[0];
	*ok = 1;
}

int main(int argc, char **argv)
{
	int port = argc > 1 ? atoi(argv[1]) : 27753;
	signal(13 /* SIGPIPE */, (void (*)(int))1 /* SIG_IGN */);
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
	notify_post("com.apple.webinspectord.enabled");
	for (;;) {
		int tcp = accept(ls, NULL, NULL);
		if (tcp < 0) {
			continue;
		}
		LOG("client connected\n");
		int svc_end = -1, ours = -1, ok = 0;
		start_service(&svc_end, &ours, &ok);
		if (ok) {
			relay(tcp, ours);
			close(ours);
		}
		close(tcp);
		LOG("client done\n");
	}
}
