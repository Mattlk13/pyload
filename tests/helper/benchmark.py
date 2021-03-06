# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, unicode_literals

from builtins import object, range, str
from time import time

from future import standard_library
standard_library.install_aliases()


class BenchmarkTest(object):

    bench = []
    results = {}

    @classmethod
    def timestamp(cls, name, a):
        t = time()
        r = cls.results.get(name, [])
        r.append((t - a) * 1000)
        cls.results[name] = r

    @classmethod
    def benchmark(cls, n=1):

        print("Benchmarking {0}".format(cls.__name__))
        print()

        for i in range(n):
            cls.collect_results()

        if "setUpClass" in cls.results:
            cls.bench.insert(0, "setUpClass")

        if "tearDownClass" in cls.results:
            cls.bench.append("tearDownClass")

        length = str(max(len(k) for k in cls.bench) + 1)
        total = 0

        for k in cls.bench:
            v = cls.results[k]

            if len(v) > 1:
                print(("{0:" + length + "} {1} | average: {2:.2f} ms").format(
                    k, ", ".join("{0:.2f}".format(x)
                                 for x in v), sum(v) // len(v)
                ))
                total += sum(v) // len(v)
            else:
                print(("{0:" + length + "}: {1:.2f} ms").format(k, v[0]))
                total += v[0]

        print("\ntotal: {0:.2f} ms".format(total))

    @classmethod
    def collect_results(cls):
        if hasattr(cls, "setUpClass"):
            a = time()
            cls.setUpClass()
            cls.timestamp("setUpClass", a)

        obj = cls()

        for f in cls.bench:
            a = time()
            getattr(obj, "test_{0}".format(f))()
            cls.timestamp(f, a)

        if hasattr(cls, "tearDownClass"):
            a = time()
            cls.tearDownClass()
            cls.timestamp("tearDownClass", a)
