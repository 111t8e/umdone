"""Main training app."""
from __future__ import unicode_literals, print_function
import os
import sys
from argparse import ArgumentParser

import urwid
import librosa
import numpy as np
import tables as tb

import umdone.io
from umdone import cli
from umdone import dtw
from umdone import sound
from umdone import segment


class TrainerModel(object):

    max_val = 1
    min_val = -1
    valid_categories = (
        (0, 'word'),
        (1, 'ummm'),
        (2, 'like'),
        (3, 'other non-word'),
        )

    def __init__(self, fname, window_length=0.05, threshold=0.01, n_mfcc=13):
        # settings
        self.filename = fname
        self.window_length = window_length
        self.threshold = threshold
        self.n_mfcc = n_mfcc

        # data
        self.current_segment = 0
        self.raw, self.sr = librosa.load(fname, mono=True, sr=None)
        self.bounds = segment.boundaries(self.raw, self.sr, window_length=window_length, 
                                         threshold=threshold)
        self.nsegments = len(self.bounds)
        self.runtime = len(self.raw) / self.sr

        # results, keyed by current segement
        self.categories = {}

    @property
    def clip(self):
        l, u = self.bounds[self.current_segment]
        return self.raw[l:u]

    def segement_order(self):
        return sorted(self.categories.keys())

    def compute_mfccs(self, callback=None):
        sr = self.sr
        n_mfcc = self.n_mfcc
        n = len(self.categories)
        self.mfccs = mfccs = []
        order = self.segement_order()
        for status, seg in enumerate(order, start=1):
            l, u = self.bounds[seg]
            clip = self.raw[l:u]
            mfcc = librosa.feature.mfcc(clip, sr, n_mfcc=n_mfcc).T
            mfccs.append(mfcc)
            if callback is not None:
                callback(status/n)
        return mfccs

    def compute_distances(self, outfile, callback=None):
        mfccs = self.mfccs
        if os.path.isfile(outfile):
            mfccs = umdone.io._load_mfccs(outfile) + mfccs
        self.distances = dtw.distance_matrix(mfccs, callback=callback)
        return self.distances

    def save(self, outfile):
        order = self.segement_order()
        cats = [self.categories[seg] for seg in order]
        umdone.io.save(outfile, self.mfccs, cats, distances=self.distances)


class TrainerView(urwid.WidgetWrap):
    """
    A class responsible for providing the application's interface and
    graph display.
    """
    palette = [
        ('body',         'black',      'light gray', 'standout'),
        ('header',       'white',      'dark red',   'bold'),
        ('screen edge',  'light blue', 'dark cyan'),
        ('main shadow',  'dark gray',  'black'),
        ('line',         'black',      'light gray', 'standout'),
        ('bg background','light gray', 'black'),
        ('bg 1',         'black',      'dark blue', 'standout'),
        ('bg 1 smooth',  'dark magenta',  'black'),
        ('bg 2',         'black',      'dark cyan', 'standout'),
        ('bg 2 smooth',  'dark cyan',  'black'),
        ('button normal','light gray', 'dark blue', 'standout'),
        ('button select','white',      'dark green'),
        ('line',         'black',      'light gray', 'standout'),
        ('pg normal',    'white',      'black', 'standout'),
        ('pg complete',  'white',      'dark magenta'),
        ('pg smooth',    'dark magenta','black'),
        ]

    graph_num_bars = 100

    def __init__(self, controller):
        self.controller = controller
        self.status = urwid.Text("Status")
        super(TrainerView, self).__init__(self.main_window())

    def update_graph(self):
        nbars = self.graph_num_bars
        d = np.abs(self.controller.model.clip)
        win_size = int(len(d) / nbars)
        d = d[:win_size*nbars]
        d.shape = (nbars, win_size)
        d = d.sum(axis=1)
        l = []
        max_value = d.max()
        for n, value in enumerate(d):  # toggle between two bar colors
            if n & 1:
                l.append([0, value])
            else:
                l.append([value, 0])
        self.graph.set_data(l, max_value)

    def update_status(self):
        model = self.controller.model
        if model.current_segment in model.categories:
            c = model.valid_categories[model.categories[model.current_segment]][1]
            c = 'Categorized as ' + c
        else:
            c = 'Uncategorized'
        s = ("Clip {0} of {1}\n"
             "Duration {2:.3} sec\n"
             "{3}"
             ).format(model.current_segment + 1, model.nsegments, 
                      len(model.clip) / model.sr, c)
        self.status.set_text(s)

    def update_progress(self):
        model = self.controller.model
        self.progress.set_completion(model.bounds[model.current_segment][0]/model.sr)

    def update_segment(self):
        self.update_graph()
        self.update_status()
        self.update_progress()

    def on_nav_button(self, button, offset):
        self.controller.offset_current_segment(offset)

    def on_cat_button(self, button, i):
        self.controller.select_category(i)

    def on_unicode_checkbox(self, w, state):
        self.graph = self.bar_graph(state)
        self.graph_wrap._w = self.graph
        self.update_graph()

    def main_shadow(self, w):
        """Wrap a shadow and background around widget w."""
        bg = urwid.AttrWrap(urwid.SolidFill("\u2592"), 'screen edge')
        shadow = urwid.AttrWrap(urwid.SolidFill(" "), 'main shadow')
        bg = urwid.Overlay(shadow, bg,
            ('fixed left', 3), ('fixed right', 1),
            ('fixed top', 2), ('fixed bottom', 1))
        w = urwid.Overlay(w, bg,
            ('fixed left', 2), ('fixed right', 3),
            ('fixed top', 1), ('fixed bottom', 2))
        return w

    def bar_graph(self, smooth=False):
        satt = None
        if smooth:
            satt = {(1,0): 'bg 1 smooth', (2,0): 'bg 2 smooth'}
        w = urwid.BarGraph(['bg background', 'bg 1', 'bg 2'], satt=satt)
        return w

    def button(self, t, fn, *args, **kwargs):
        w = urwid.Button(t, fn, *args, **kwargs)
        w = urwid.AttrWrap(w, 'button normal', 'button select')
        return w

    def progress_bar(self, done=1, smooth=False):
        if smooth:
            return urwid.ProgressBar('pg normal', 'pg complete', 0, done, 'pg smooth')
        else:
            return urwid.ProgressBar('pg normal', 'pg complete', 0, done)

    def save_and_exit_program(self, w):
        # replace progress bar
        self.progress = self.progress_bar(done=1.0)
        self.progress_wrap._w = self.progress
        # save and exit
        self.controller.save()
        self.exit_program(w)

    def exit_program(self, w):
        raise urwid.ExitMainLoop()

    def graph_controls(self):
        # setup category buttons
        vc = self.controller.model.valid_categories
        self.category_buttons = [self.button(cat, self.on_cat_button, i) 
                                 for i, cat in vc]
        # setup animate button
        nav_controls = urwid.GridFlow([
            self.button(" prev ", self.on_nav_button, -1),
            self.button("replay", self.on_nav_button, 0),
            self.button(" next ", self.on_nav_button, 1),
            ], 10, 3, 0, 'center')

        self.progress = self.progress_bar(done=self.controller.model.runtime)
        self.progress_wrap = urwid.WidgetWrap(self.progress)

        l = [urwid.Text("Categories", align="center")]
        l += self.category_buttons
        l += [urwid.Divider(),
              urwid.Text("Navigation", align="center"),
              nav_controls,
              urwid.Divider(),
              urwid.LineBox(self.status),
              urwid.Divider(),
              self.progress_wrap,
              urwid.Divider(),
              self.button("Save and quit", self.save_and_exit_program),
              self.button("Quit without saving", self.exit_program),
              ]
        w = urwid.ListBox(urwid.SimpleListWalker(l))
        return w

    def main_window(self):
        self.graph = self.bar_graph()
        self.graph_wrap = urwid.WidgetWrap(self.graph)
        vline = urwid.AttrWrap(urwid.SolidFill('\u2502'), 'line')
        c = self.graph_controls()
        w = urwid.Columns([('weight', 1, self.graph_wrap),
                           ('fixed', 1, vline), (42, c)],
                           dividechars=1, focus_column=2)
        w = urwid.Padding(w, ('fixed left', 1), ('fixed right', 1))
        w = urwid.AttrWrap(w,'body')
        w = urwid.LineBox(w)
        w = urwid.AttrWrap(w,'line')
        w = self.main_shadow(w)
        return w


class TrainerDisplay(object):

    def __init__(self, ns):
        self.ns = ns
        self.model = TrainerModel(ns.input, window_length=ns.window_length, 
                                  threshold=ns.noise_threshold, n_mfcc=ns.n_mfcc)
        self.view = TrainerView(self)
        self.view.update_segment()

    def select_category(self, cat):
        s = self.model.current_segment 
        self.model.categories[s] = cat
        self.select_segment(s+1)

    def select_segment(self, s):
        if s < 0:
            s = 0
        elif s >= self.model.nsegments:
            s = self.model.nsegments - 1
        self.model.current_segment = s
        clip = self.model.clip
        self.view.update_segment()
        self.loop.set_alarm_in(0.001, lambda w, d: sound.play(clip, self.model.sr))

    def offset_current_segment(self, offset):
        s = self.model.current_segment
        s += offset
        self.select_segment(s)

    def save(self):
        model = self.model
        view = self.view
        view.status.set_text('\nComputing MFCCs\n')
        model.compute_mfccs(view.progress.set_completion)
        view.status.set_text('\nComputing distance matrix\n')
        model.compute_distances(self.ns.output, view.progress.set_completion)
        view.status.set_text('\nSaving\n')
        model.save(self.ns.output)

    def main(self):
        self.loop = urwid.MainLoop(self.view, self.view.palette, pop_ups=True)
        self.loop.set_alarm_in(0.001, lambda w, d: self.select_segment(0))
        self.loop.run()


def add_arguments(parser):
    cli.add_output(parser)
    cli.add_window_length(parser)
    cli.add_noise_threshold(parser)
    cli.add_n_mfcc(parser)
    cli.add_input(parser)


def main(ns=None, args=None):
    """Entry point for umdone trainer."""
    if ns is None:
        parser = ArgumentParser('umdone-trainer')
        add_arguments(parser)
        ns = parser.parse_args(args)
    if ns.output is None:
        ns.output = '{0}-umdone-training.h5'.format(os.path.splitext(ns.input)[0])
    TrainerDisplay(ns).main()


if __name__ == '__main__':
    main()
