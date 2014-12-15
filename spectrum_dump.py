### GStreamer Spectrum Dump ####
# Modified for hyperion effect by RanzQ
# ranzq87 [(at)] gmail.com
#
# Original:
# https://github.com/Wintervenom/gst-spectrumdump
# V20111005-1 by Scott Garrett
# Wintervenom [(at)] gmail.com
################################
# Dependencies:
# gi (python-gst0.10)
#
# Optional Dependencies:
# gconf (python2-gconf)
#
#################################

# try:
#     import gconf
# except ImportError:
#     pass
import sys

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst, GLib

import math

GObject.threads_init()
Gst.init(None)

# VERSION = 20111005-1
#sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)

def stdout(message):
    """
    Writes a message to STDOUT.
    """
    sys.stdout.write("{0}\n".format(message))
    sys.stdout.flush()


def stderr(message):
    """
    Writes a message to STDERR.
    """
    sys.stderr.write("{0}\n".format(message))
    sys.stderr.flush()


def fatal(error):
    """
    Output an error message to STDERR and exit with status 1.
    """
    stderr("Error: {0}".format(error))
    sys.exit(1)



class GstSpectrumDump(object):
    """
    Dumps the spectrum magnitudes of incoming audio as volume units per band.

    Optional arguments:
    <source>        Source of the audio (default: alsasrc or gconf setting).
    <precision>     How many decimal places to round the magnitudes to
                    (default: 16).
    <bands>         How many frequency bands to output (default: 128).
    <amplify>       Amplify output by this much (default: 1).
    <logamplify>    Amplify magnitude values logarithmically to compensate for
                    softer higher frequencies.  (default: False)
    <autoamp>       Automatically control amplification levels when they are
                    too loud.
    <threshold>     Minimal magnitude of a band in decibels (default: 70).
    <cufoff>        Cut off magnitudes at this value after amplification has
                    been applied (default: 100).
    <scale>         Scale magnitudes to this value (default: 100).
    <raw>           Don't clip or apply logarithmic upscale the output.
                    (default: True).
    <db>            Return output in decibels instead of a percentage.
                    <logamplify> is ignored (default: True).
    <iec>           Convert decibels to percentages with IEC 60268-18 scaling
                    (default: False).
    <vumeter>       Return VU meter output instead of spectrum.  <bands>
                    controls how many channels to output here.  <threshold> is
                    ignored.
    <interval>      Milliseconds to wait between polls (default: 50).
    <quiet>         Don't output to STDERR (default: False if no callback).
    <callback>      Return the magnitude list to this function (default: None).
    """
    def __init__(self, **opts):
        self.running = False
        self.source = opts.get('source')
        self.precision = opts.get('precision')
        self.bands = opts.get('bands', 128)
        self.amplify = opts.get('amplify', 1)
        self.logamplify = opts.get('logamplify', False)
        self.autoamp = opts.get('autoamp', False)
        self.threshold = opts.get('threshold', 70)
        self.cutoff = opts.get('cutoff', 100)
        self.scaleto = opts.get('scale', 100)
        self.raw = opts.get('raw', True)
        self.db = opts.get('db', False)
        self.iec = opts.get('iec', False)
        self.vumeter = opts.get('vumeter', False)
        self.interval = opts.get('interval', 50)
        self.callback = opts.get('callback')
        self.quiet = opts.get('quiet', self.callback is not None)
        self.pipeline = None
        self.gainhits = 0
        self.origamp = self.amplify
        if not self.source:
            self.source = 'autoaudiosrc'
            # defaultsrc = 'alsasrc'
            # try:
            #     conf = gconf.client_get_default()
            #     source = conf.get('/system/gstreamer/%d.%d/default/audiosrc' %
            #                       Gst.gst_version[:-1])
            #     if source:
            #         self.source = source.get_string()
            #     else:
            #         self.source = defaultsrc
            # except NameError:
            #     stderr('Python2 GConf module not installed; using default source.')
            #     self.source = defaultsrc
        elif self.source.startswith('mpd'):
            fifo = self.source.split(' ', 1)
            fifo = fifo[1] if len(fifo) > 1 else '/tmp/mpd.fifo'
            pipeline = 'filesrc location={} ! audio/x-raw-int, ' \
                       'rate=44100, channels=2, endianness=1234, width=16, ' \
                       'depth=16, signed=true ! audioconvert'
            self.source = pipeline.format(fifo)


    def round(self, n):
        if self.precision:
            return round(n, self.precision)
        elif self.precision == 0:
            return int(n)
        return n


    def dbtopct(self, db, index=None):
        indexamp = 1
        if self.iec:
            pct = 0.0
            if db < -70.0:
                pct = 0.0
            elif db < -60.0:
                pct = (db + 70.0) * 0.25
            elif db < -50.0:
                pct = (db + 60.0) * 0.5 + 2.5
            elif db < -40.0:
                pct = (db + 50.0) * 0.75 + 7.5
            elif db < -30.0:
                pct = (db + 40.0) * 1.5 + 15.0
            elif db < -20.0:
                pct = (db + 30.0) * 2.0 + 30.0
            elif db < 0.0:
                pct = (db + 20.0) * 2.5 + 50.0
            else:
                pct = 100.0
        else:
            pct = (self.threshold + db) / float(self.threshold) * 100
        if index and index > 0:
            indexamp += math.log10(index)
        pct = min(self.cutoff, self.amplify * (indexamp * pct))
        if self.autoamp:
            if pct == 100:
                self.gainhits += 1
                if self.amplify > 0:
                    self.amplify -= 0.1
            elif pct == 0:
                self.gainhits -= 1
                if self.gainhits < -100:
                    if self.amplify < self.origamp:
                        self.amplify += 0.01
                    self.gainhits = 0
        return pct * (self.scaleto / 100.0)


    def interpolate(self, a, b, points):
        points = round(points, 0) + 1.0
        return [a + ((b - a) / points) * x for x in range(0, int(points))]


    def scale(self, floats, maxlen=None):
        if len(floats) < 2:
            return floats
        scaled = []
        for i in range(1, len(floats)):
            length = 1 + math.log10(i - 0)
            scaled += self.interpolate(floats[i-1], floats[i], length)[:-1]
        scaled.append(floats[-1])
        if maxlen and len(scaled) > maxlen:
            downscaled = []
            incr = len(scaled) / float(maxlen)
            index = 0
            for v in range(0, maxlen):
                downscaled.append(scaled[int(round(index, 0))])
                index += incr
            return downscaled
        else:
            return scaled


    def on_message(self, bus, message):

        # We should return false if the pipeline has stopped
        if not self.running:
            return False

        print message

        try:
            s = message.structure
            name = s.get_name()
            if name == 'spectrum':
                if self.bands > 40:
                    cutoff = int(round(self.bands * (7/8.0), 0))
                else:
                    cutoff = None
                magnitudes = s['magnitude'][0][:cutoff]
                if not self.db:
                    if self.logamplify:
                        magnitudes = [self.dbtopct(db, i) for i, db
                                      in enumerate(magnitudes)]
                    else:
                        magnitudes = [self.dbtopct(db) for i, db
                                      in enumerate(magnitudes)]
                if not self.raw:
                    magnitudes = self.scale(magnitudes, self.bands)
                magnitudes = [self.round(m) for m in magnitudes]
            elif name == 'level':
                magnitudes = []
                for channel in range(0, min(self.bands, len(s['peak']))):
                    peak = max(-self.threshold, min(0, s['peak'][channel]))
                    decay = max(-self.threshold, min(0, s['decay'][channel]))
                    if not self.db:
                        if self.logamplify:
                            peak = self.dbtopct(peak, peak)
                            decay = self.dbtopct(decay, decay)
                        else:
                            peak = self.dbtopct(peak)
                            decay = self.dbtopct(decay)
                    magnitudes.append(self.round(peak))
                    magnitudes.append(self.round(decay))
            else:
                return True
            if not self.quiet:
                try:
                    print(' '.join((str(m) for m in magnitudes)))
                except IOError:
                    self.loop.quit()

            if self.callback:
                self.callback(magnitudes)
        except KeyboardInterrupt:
            self.loop.quit()
        return True


    def start_pipeline(self):
        self.running = True
        pipeline = [self.source]
        interval = 'interval={0}'.format(1000000 * self.interval)
        if self.vumeter:
            pipeline.append('level message=true {}'.format(interval))
        else:
            spectrum = 'spectrum message=true {} bands={} threshold=-{} multi-channel=true'
            spectrum = spectrum.format(interval, self.bands, self.threshold)
            pipeline.append(spectrum)
        pipeline.append('fakesink sync=false')
        self.pipeline = Gst.parse_launch(' ! '.join(pipeline))
        # self.pipeline = Gst.Pipeline()
        # for element in pipeline:
        #     self.pipeline.add(element)

        self.bus = self.pipeline.get_bus()
        self.bus.enable_sync_message_emission()
        self.bus.add_signal_watch()
        # self.conn = self.bus.connect("message::element", self.on_message)
        self.source_id = self.bus.add_watch(GLib.PRIORITY_DEFAULT, self.on_message, None)
        stdout("Bus connected.")
        self.pipeline.set_state(Gst.State.PLAYING)
        stdout("Pipeline STATE_PLAYING set.")


    def stop_pipeline(self):
        self.running = False
        if self.pipeline:
            # self.bus.disconnect(self.conn)
            # GLib.Source.remove(self.source_id) # Not working?
            stdout("Bus disconnected.")
            self.bus.remove_signal_watch()
            stdout("Signal watch removed.")
            self.pipeline.set_state(Gst.State.NULL)
            stdout("Pipeline STATE_NULL set.")


    def start(self):
        self.start_pipeline()
        self.loop = GLib.MainLoop()
        self.loop_context = self.loop.get_context()
        stdout("Pipeline initialized.")


    def iterate(self):
        self.loop_context.iteration(False) # True = Block until any events dispatch


    def stop(self):
        stdout("Stopping pipeline...")
        self.stop_pipeline()
        stdout("Done.")
