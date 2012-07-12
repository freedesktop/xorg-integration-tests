#include <stdexcept>
#include <map>
#include <xorg/gtest/xorg-gtest.h>
#include <linux/input.h>

#include <X11/Xlib.h>
#define XK_LATIN1
#include <X11/keysymdef.h>
#include <X11/XF86keysym.h>
#include <X11/Xutil.h>
#include <X11/extensions/XInput.h>
#include <X11/extensions/XInput2.h>

#include "input-driver-test.h"

typedef std::pair <int, KeySym> Key_Pair;
typedef std::multimap<std::string, Key_Pair> Keys_Map;
typedef Keys_Map::iterator keys_mapIter;
typedef std::vector<Key_Pair> MultiMedia_Keys_Map;
typedef MultiMedia_Keys_Map::iterator multimediakeys_mapIter;

class EvdevDriverXKBTest : public InputDriverTest {
    virtual void SetUp() {

        dev = std::auto_ptr<xorg::testing::evemu::Device>(
                new xorg::testing::evemu::Device(
                    RECORDINGS_DIR "keyboards/AT Translated Set 2 Keyboard.desc"
                    )
                );

        // Define a map of pair to hold each key/keysym per layout
        // US, QWERTY => qwerty
        Keys.insert (std::pair<std::string, Key_Pair> ("us", Key_Pair (KEY_Q, XK_q)));
        Keys.insert (std::pair<std::string, Key_Pair> ("us", Key_Pair (KEY_W, XK_w)));
        Keys.insert (std::pair<std::string, Key_Pair> ("us", Key_Pair (KEY_E, XK_e)));
        Keys.insert (std::pair<std::string, Key_Pair> ("us", Key_Pair (KEY_R, XK_r)));
        Keys.insert (std::pair<std::string, Key_Pair> ("us", Key_Pair (KEY_T, XK_t)));
        Keys.insert (std::pair<std::string, Key_Pair> ("us", Key_Pair (KEY_Y, XK_y)));
        // German, QWERTY => qwertz
        Keys.insert (std::pair<std::string, Key_Pair> ("de", Key_Pair (KEY_Q, XK_q)));
        Keys.insert (std::pair<std::string, Key_Pair> ("de", Key_Pair (KEY_W, XK_w)));
        Keys.insert (std::pair<std::string, Key_Pair> ("de", Key_Pair (KEY_E, XK_e)));
        Keys.insert (std::pair<std::string, Key_Pair> ("de", Key_Pair (KEY_R, XK_r)));
        Keys.insert (std::pair<std::string, Key_Pair> ("de", Key_Pair (KEY_T, XK_t)));
        Keys.insert (std::pair<std::string, Key_Pair> ("de", Key_Pair (KEY_Y, XK_z)));
        // French, QWERTY => azerty
        Keys.insert (std::pair<std::string, Key_Pair> ("fr", Key_Pair (KEY_Q, XK_a)));
        Keys.insert (std::pair<std::string, Key_Pair> ("fr", Key_Pair (KEY_W, XK_z)));
        Keys.insert (std::pair<std::string, Key_Pair> ("fr", Key_Pair (KEY_E, XK_e)));
        Keys.insert (std::pair<std::string, Key_Pair> ("fr", Key_Pair (KEY_R, XK_r)));
        Keys.insert (std::pair<std::string, Key_Pair> ("fr", Key_Pair (KEY_T, XK_t)));
        Keys.insert (std::pair<std::string, Key_Pair> ("fr", Key_Pair (KEY_Y, XK_y)));

        // Define a vector of pair to hold each key/keysym for multimedia
        Multimedia_Keys.push_back (Key_Pair (KEY_MUTE,           XF86XK_AudioMute));
        Multimedia_Keys.push_back (Key_Pair (KEY_VOLUMEUP,       XF86XK_AudioRaiseVolume));
        Multimedia_Keys.push_back (Key_Pair (KEY_VOLUMEDOWN,     XF86XK_AudioLowerVolume));
        Multimedia_Keys.push_back (Key_Pair (KEY_PLAYPAUSE,      XF86XK_AudioPlay));
        Multimedia_Keys.push_back (Key_Pair (KEY_NEXTSONG,       XF86XK_AudioNext));
        Multimedia_Keys.push_back (Key_Pair (KEY_PREVIOUSSONG,   XF86XK_AudioPrev));

        InputDriverTest::SetUp();
    }

    virtual void SetUpConfigAndLog(const std::string &prefix) {
        server.SetOption("-logfile", "/tmp/Xorg-evdev-driver-xkb.log");
        server.SetOption("-config", "/tmp/evdev-driver-xkb.conf");

        config.AddDefaultScreenWithDriver();
        config.AddInputSection("evdev", "--device--",
                               "Option \"CoreKeyboard\" \"on\"\n"
                               "Option \"XkbRules\"   \"xorg\"\n"
                               "Option \"XkbModel\"   \"dellusbmm\"\n"
                               "Option \"XkbLayout\"  \""+ prefix + "\"\n"
                               "Option \"Device\" \"" + dev->GetDeviceNode() + "\"");
        config.WriteConfig("/tmp/evdev-driver-xkb.conf");
    }

    protected:
    std::auto_ptr<xorg::testing::evemu::Device> dev;
    Keys_Map Keys;
    MultiMedia_Keys_Map Multimedia_Keys;
};


TEST_P(EvdevDriverXKBTest, DeviceExists)
{
    std::string param;
    int ndevices;
    XIDeviceInfo *info;

    info = XIQueryDevice(Display(), XIAllDevices, &ndevices);
    bool found = false;
    while(ndevices--) {
        if (strcmp(info[ndevices].name, "--device--") == 0) {
            ASSERT_EQ(found, false) << "Duplicate device" << std::endl;
            found = true;
        }
    }

    ASSERT_EQ(found, true);

    XIFreeDeviceInfo(info);
}

void play_key_pair (::Display *display, xorg::testing::evemu::Device *dev, Key_Pair pair)
{
    dev->PlayOne(EV_KEY, pair.first, 1, 1);
    dev->PlayOne(EV_KEY, pair.first, 0, 1);

    XSync(display, False);
    ASSERT_NE(XPending(display), 0) << "No event pending" << std::endl;

    XEvent press;
    XNextEvent(display, &press);

    KeySym sym = XKeycodeToKeysym(display, press.xkey.keycode, 0);
    ASSERT_NE(NoSymbol, sym) << "No keysym for keycode " << press.xkey.keycode << std::endl;
    ASSERT_EQ(pair.second, sym) << "Keysym not matching for keycode " << press.xkey.keycode << std::endl;

    XSync(display, False);
    while (XPending(display))
      XNextEvent(display, &press);
}

TEST_P(EvdevDriverXKBTest, KeyboardLayout)
{
    std::string layout = GetParam();

    XSelectInput(Display(), DefaultRootWindow(Display()), KeyPressMask | KeyReleaseMask);
    /* the server takes a while to start up bust the devices may not respond
       to events yet. Add a noop call that just delays everything long
       enough for this test to work */
    XInternAtom(Display(), "foo", True);
    XFlush(Display());

    keys_mapIter it;
    std::pair<keys_mapIter, keys_mapIter> keyRange = Keys.equal_range(layout);
    for (it = keyRange.first;  it != keyRange.second;  ++it)
        play_key_pair (Display(), dev.get(), (*it).second);

    // Now test multimedia keys
    multimediakeys_mapIter m_it;
    for (m_it = Multimedia_Keys.begin(); m_it != Multimedia_Keys.end(); m_it++)
        play_key_pair (Display(), dev.get(), (*m_it));
}

INSTANTIATE_TEST_CASE_P(, EvdevDriverXKBTest, ::testing::Values("us", "de", "fr"));

class EvdevDriverMouseTest : public InputDriverTest {
public:
    virtual void SetUp() {

        dev = std::auto_ptr<xorg::testing::evemu::Device>(
                new xorg::testing::evemu::Device(
                    RECORDINGS_DIR "mice/PIXART USB OPTICAL MOUSE.desc"
                    )
                );
        InputDriverTest::SetUp();
    }

    virtual void SetUpConfigAndLog(const std::string &prefix) {
        server.SetOption("-logfile", "/tmp/Xorg-evdev-driver-mouse.log");
        server.SetOption("-config", "/tmp/evdev-driver-mouse.conf");

        config.AddDefaultScreenWithDriver();
        config.AddInputSection("evdev", "--device--",
                               "Option \"CorePointer\" \"on\"\n"
                               "Option \"Device\" \"" + dev->GetDeviceNode() + "\"");
        config.WriteConfig("/tmp/evdev-driver-mouse.conf");
    }

protected:
    std::auto_ptr<xorg::testing::evemu::Device> dev;
};

void scroll_wheel_event(::Display *display,
                        xorg::testing::evemu::Device *dev,
                        int value, int button) {

    dev->PlayOne(EV_REL, REL_WHEEL, value, 1);

    XSync(display, False);

    ASSERT_NE(XPending(display), 0) << "No event pending" << std::endl;
    XEvent btn;
    int nevents = 0;
    while(XPending(display)) {
        XNextEvent(display, &btn);

        ASSERT_EQ(btn.xbutton.type, ButtonPress);
        ASSERT_EQ(btn.xbutton.button, button);

        XNextEvent(display, &btn);
        ASSERT_EQ(btn.xbutton.type, ButtonRelease);
        ASSERT_EQ(btn.xbutton.button, button);

        nevents++;
    }

    ASSERT_EQ(nevents, abs(value));
}


TEST_F(EvdevDriverMouseTest, ScrollWheel)
{
    XSelectInput(Display(), DefaultRootWindow(Display()), ButtonPressMask | ButtonReleaseMask);
    /* the server takes a while to start up bust the devices may not respond
       to events yet. Add a noop call that just delays everything long
       enough for this test to work */
    XInternAtom(Display(), "foo", True);
    XFlush(Display());

    scroll_wheel_event(Display(), dev.get(), 1, 4);
    scroll_wheel_event(Display(), dev.get(), 2, 4);
    scroll_wheel_event(Display(), dev.get(), 3, 4);

    scroll_wheel_event(Display(), dev.get(), -1, 5);
    scroll_wheel_event(Display(), dev.get(), -2, 5);
    scroll_wheel_event(Display(), dev.get(), -3, 5);
}

int main(int argc, char **argv) {
    testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}