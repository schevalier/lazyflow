###############################################################################
#   lazyflow: data flow based lazy parallel computation framework
#
#       Copyright (C) 2011-2014, the ilastik developers
#                                <team@ilastik.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the Lesser GNU General Public License
# as published by the Free Software Foundation; either version 2.1
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# See the files LICENSE.lgpl2 and LICENSE.lgpl3 for full text of the
# GNU Lesser General Public License version 2.1 and 3 respectively.
# This information is also available on the ilastik web site at:
#		   http://ilastik.org/license/
###############################################################################
# Built-in
import sys
import functools
import itertools
import collections
import threading
import multiprocessing
import platform
import traceback
import StringIO

import logging
logger = logging.getLogger(__name__)

# Third-party
import greenlet

# lazyflow
import threadPool

# This module's code needs to be sanitized if you're not using CPython.
# In particular, check that set operations like remove() are still atomic.
assert platform.python_implementation() == "CPython"

class RequestGreenlet(greenlet.greenlet):
    def __init__(self, owning_request, fn):
        super(RequestGreenlet, self).__init__(fn, greenlet.getcurrent())
        self.owning_requests = [owning_request]

class SimpleSignal(object):
    """
    Simple callback mechanism. Not synchronized.  No unsubscribe function.
    """
    def __init__(self):
        self.callbacks = []
        self._cleaned = False

    def subscribe(self, fn):
        self.callbacks.append(fn)

    def __call__(self, *args, **kwargs):
        """Emit the signal."""
        assert not self._cleaned, "Can't emit a signal after it's already been cleaned!"
        for f in self.callbacks:
            f(*args, **kwargs)
        
    def clean(self):
        self._cleaned = True
        self.callbacks = []

def log_exception( logger, msg=None, exc_info=None, level=logging.ERROR ):
    """
    Log the current exception to the given logger, and also log the given error message.
    If exc_info is provided, log that exception instead of the current exception provided by sys.exc_info.
    
    It is better to log exceptions this way instead of merely printing them to the console, 
    so that other logger outputs (such as log files) show the exception, too.
    """
    sio = StringIO.StringIO()
    if exc_info:
        traceback.print_exception( exc_info[0], exc_info[1], exc_info[2], file=sio )
    else:
        traceback.print_exc( file=sio )

    logger.log(level, sio.getvalue() )
    if msg:
        logger.log(level, msg )

class Request( object ):
    
    # One thread pool shared by all requests.
    # See initialization after this class definition (below)
    global_thread_pool = None
    
    @classmethod
    def reset_thread_pool( cls, num_workers = multiprocessing.cpu_count() ):
        """
        Change the number of threads allocated to the request system.

        As a special case, you may set ``num_workers`` to 0.  
        In that case, the normal thread pool is not used at all.  
        Instead, all requests will execute synchronously, from within the submitting thread.  
        Utilities like ``RequestLock``, ``SimpleRequestCondition`` will use alternate 
        implementations based on equivalent classes in the builtin ``threading`` module. 
        
        .. note:: It is only valid to call this function during startup.
                  Any existing requests will be dropped from the pool!
        """
        if cls.global_thread_pool is not None:
            cls.global_thread_pool.stop()
        cls.global_thread_pool = threadPool.ThreadPool( num_workers )
    
    class CancellationException(Exception):
        """
        This is raised when the whole request has been cancelled.
        If you catch this exception from within a request, clean up and return immediately.
        If you have nothing to clean up, you are not required to handle this exception.
        
        Implementation details:
        This exception is raised when the cancel flag is checked in the wait() function:
        - immediately before the request is suspended OR
        - immediately after the request is woken up from suspension
        """
        pass

    class InvalidRequestException(Exception):
        """
        This is raised when calling wait on a request that has already been cancelled,
        which can only happen if the request you're waiting for was spawned elsewhere 
        (i.e. you are waiting for someone else's request to avoid duplicate work).
        When this occurs, you will typically want to restart the request yourself.
        """
        pass

    class CircularWaitException(Exception):
        """
        This exception is raised if a request calls wait() on itself.
        Currently, this only catches the most basic case.
        No attempt is made to detect indirect cycles
        (e.g. if req.wait() is called from within a req's own child.),
        so don't rely on it to catch tricky deadlocks due to indirect self-waiting.
        """
        pass
    
    class TimeoutException(Exception):
        """
        This is raised if a call to wait() times out in the context of a foreign thread.
        See ``Request.wait()`` for details.
        """
        pass
    
    class InternalError(Exception):
        """
        This is raised if an error is detected in the Request framework itself.
        If this exception is raised, it implies a bug in this file (request.py).
        """
        pass
    
    _root_request_counter = itertools.count()

    def __init__(self, fn):
        """
        Constructor.
        Postconditions: The request has the same cancelled status as its parent (the request that is creating this one).
        """
        # Workload
        self.fn = fn

        #: After this request finishes execution, this attribute holds the return value from the workload function.
        self._result = None

        # State
        self.started = False
        self.cancelled = False
        self.uncancellable = False
        self.finished = False
        self.execution_complete = False
        self.finished_event = threading.Event()
        self.exception = None
        self.exception_info = (None, None, None)
        self._cleaned = False

        # Execution
        self.greenlet = None # Not created until assignment to a worker
        self._assigned_worker = None

        # Request relationships
        self.pending_requests = set()  # Requests that are waiting for this one
        self.blocking_requests = set() # Requests that this one is waiting for (currently one at most since wait() can only be called on one request at a time)
        self.child_requests = set()    # Requests that were created from within this request (NOT the same as pending_requests)
        
        self._current_foreign_thread = None
        current_request = Request._current_request()
        self.parent_request = current_request
        if current_request is None:
            self._priority = [ Request._root_request_counter.next() ]
        else:
            with current_request._lock:
                current_request.child_requests.add(self)
                # We must ensure that we get the same cancelled status as our parent.
                self.cancelled = current_request.cancelled
                # We acquire the same priority as our parent, plus our own sub-priority
                self._priority = current_request._priority + [ len(current_request.child_requests) ]

        self._lock = threading.Lock() # NOT an RLock, since requests may share threads
        self._sig_finished = SimpleSignal()
        self._sig_cancelled = SimpleSignal()
        self._sig_failed = SimpleSignal()
        
        self._sig_execution_complete = SimpleSignal()

    def __lt__(self, other):
        """
        Request comparison is by priority.
        This allows us to store them in a heap.
        """
        return self._priority < other._priority

    def __str__(self):
        return "fn={}, assigned_worker={}, started={}, execution_complete={}, exception={}, "\
               "greenlet={}, current_foreign_thread={}, uncancellable={}"\
               .format( self.fn, self.assigned_worker, self.started, self.execution_complete, \
                        self.exception, self.greenlet, self._current_foreign_thread, self.uncancellable )

    def clean(self, _fullClean=True):
        """
        Delete all state from the request, for cleanup purposes.
        Removes references to callbacks, children, and the result.
        
        :param _fullClean: Internal use only.  If False, only clean internal bookkeeping members.
                           Otherwise, delete everything, including the result.
        """
        self._sig_cancelled.clean()
        self._sig_finished.clean()
        self._sig_failed.clean()

        with self._lock:
            for child in self.child_requests:
                child.parent_request = None
            self.child_requests.clear()

        if _fullClean:
            self._cleaned = True
            self._result = None
        
    @property
    def assigned_worker(self):
        """
        This member is accessed by the ThreadPool to determine which Worker thread this request belongs to.
        """
        return self._assigned_worker
    
    @assigned_worker.setter
    def assigned_worker(self, worker):
        """
        Assign this request to the given worker thread.  (A request cannot switch between threads.)
        Must be called from the worker thread.
        """
        assert self._assigned_worker is None
        self._assigned_worker = worker

        # Create our greenlet now (so the greenlet has the correct parent, i.e. the worker)
        self.greenlet = RequestGreenlet(self, self._execute)

    @property
    def result(self):
        assert not self._cleaned, "Can't get this result.  The request has already been cleaned!"
        assert self.execution_complete, "Can't access the result until the request is complete."
        assert not self.cancelled, "Can't access the result of a cancelled request."
        assert self.exception is None, "Can't access this result.  The request failed."
        return self._result

    def _execute(self):
        """
        Do the real work of this request.
        """
        # Did someone cancel us before we even started?
        if not self.cancelled:
            try:
                # Do the actual work
                self._result = self.fn()
            except Request.CancellationException:
                # Don't propagate cancellations back to the worker thread,
                # even if the user didn't catch them.
                pass
            except Exception as ex:
                # The workload raised an exception.
                # Save it so we can raise it in any requests that are waiting for us.
                self.exception = ex
                self.exception_info = sys.exc_info()    # Documentation warns of circular references here,
                                                        #  but that should be okay for us.
        self._post_execute()

    def _post_execute(self):
        # Guarantee that self.finished doesn't change while wait() owns self._lock
        with self._lock:
            self.finished = True

        try:
            # Notify callbacks (one or the other, not both)
            if self.cancelled:
                self._sig_cancelled()
            elif self.exception is not None:
                self._sig_failed( self.exception, self.exception_info )
            else:
                self._sig_finished(self._result)

        except Exception as ex:
            # If we're here, then our completion handler function (e.g. sig_finished or sig_failed)
            #  raised an exception.
            failed_during_failure_handler = (self.exception is not None)

            # Save the exception so we can re-raise it in any requests that are waiting for us.
            # Otherwise, the threadpool just dies.
            self.exception = ex
            self.exception_info = sys.exc_info()    # Documentation warns of circular references here,
                                                    #  but that should be okay for us.
                
            # If we already fired sig_failed(), then there's no point in firing it again.
            #  That's the function that caused this problem in the first place!
            if not failed_during_failure_handler:
                self._sig_failed( self.exception, self.exception_info )

        else:
            # Now that we're complete, the signals have fired and any requests we needed to wait for have completed.
            # To free memory (and child requests), we can clean up everything but the result.
            self.clean( _fullClean=False )

        finally:
            # Unconditionally signal (internal use only)
            with self._lock:
                self.execution_complete = True
                self._sig_execution_complete()
                self._sig_execution_complete.clean()

            # Notify non-request-based threads
            self.finished_event.set()

            # Clean-up
            if self.greenlet is not None:
                popped = self.greenlet.owning_requests.pop()
                assert popped == self
            self.greenlet = None

    def submit(self):
        """
        If this request isn't started yet, schedule it to be started.
        """
        if Request.global_thread_pool.num_workers > 0:
            with self._lock:
                if not self.started:
                    self.started = True
                    self._wake_up()
        else:
            # For debug purposes, we support a worker count of zero.
            # In that case, ALL REQUESTS ARE synchronous.
            # This can have unintended consequences.  Use with care.
            if not self.started:
                self.started = True
                self._execute()

            # TODO: Exactly how to handle cancellation in this debug mode is not quite clear...

            # The _execute() function normally intercepts exceptions to hide them from the worker threads.
            # In this debug mode, we want to re-raise the exception.
            if self.exception is not None:
                raise self.exception_info[0], self.exception_info[1], self.exception_info[2]

    def _wake_up(self):
        """
        Resume this request's execution (put it back on the worker's job queue).
        """
        Request.global_thread_pool.wake_up(self)
 
    def _switch_to(self):
        """
        Switch to this request's greenlet
        """
        try:
            self.greenlet.switch()
        except greenlet.error:
            # This is a serious error.
            # If we are handling an exception here, it means there's a bug in the request framework,
            #  not the client's code.
            msg = "Current thread ({}) could not start/resume task: {}"\
                  .format( threading.current_thread().name, self )
            log_exception( logger, msg, level=logging.CRITICAL )
            
            # We still run the post-execute code, so that all requests waiting on this 
            #  one will be notified of the error and produce their own tracebacks.
            # Hopefully that will help us reproduce/debug the issue.
            self.exception = Request.InternalError( "A serious error was detected while waiting for another request.  "
                                                    "Check the log for other exceptions." )
            self.exception_info = ( type(self.exception), 
                                    self.exception, 
                                    sys.exc_info()[2] ) 
            self._post_execute()
            
            # And now we simply return instead of letting this worker die.

    #def __call__(self):
    #    """
    #    Resume (or start) the request execution.
    #    This is implemented in __call__ so that it can be used with the ThreadPool, which is designed for general callable tasks.
    #    
    #    .. note:: DO NOT use ``Request.__call__`` explicitly from your code.  It is called internally or from the ThreadPool.
    #    """
    #    self._switch_to()
    
    # Implement __call__ with a direct assignment instead of the 
    #  above implementation to avoid an unecessary function call.
    __call__ = _switch_to
        
    def _suspend(self):
        """
        Suspend this request so another one can be woken up by the worker.
        """
        # Switch back to the worker that we're currently running in.
        try:
            self.greenlet.parent.switch()
        except greenlet.error:
            logger.critical( "Current thread ({}) could not suspend task: {}.  (parent greenlet={})"
                             .format( threading.current_thread().name, self, self.greenlet.parent ) )
            raise

    def wait(self, timeout=None):
        """
        Start this request if necessary, then wait for it to complete.  Return the request's result.
        
        :param timeout: If running within a request, this parameter must be None.
                        If running within the context of a foreign (non-request) thread, 
                        a timeout may be specified in seconds (floating-point).
                        If the request does not complete within the timeout period, 
                        then a Request.TimeoutException is raised.
        """        
        assert not self._cleaned, "Can't wait() for a request that has already been cleaned."
        return self._wait(timeout)

    def block(self, timeout=None):
        """
        Like wait, but does not return a result.  Can be used even if the request has already been cleaned.
        """
        self._wait(timeout) # No return value. Use wait()

    def _wait(self, timeout=None):
        # Quick shortcut:
        # If there's no need to wait, just return immediately.
        # This avoids some function calls and locks.
        # (If we didn't do this, the code below would still do the right thing.)
        # Note that this is only possible because self.execution_complete is set to True 
        #  AFTER self.cancelled and self.exception have their final values.  See _execute().
        if self.execution_complete and not self.cancelled and self.exception is None:
            return self._result
        
        # Identify the request that is waiting for us (the current context)
        current_request = Request._current_request()

        if current_request is None:
            # 'None' means that this thread is not one of the request worker threads.
            self._wait_within_foreign_thread( timeout )
        else:
            assert timeout is None, "The timeout parameter may only be used when wait() is called from a foreign thread."
            self._wait_within_request( current_request )

        assert self.finished
        return self._result

    def _wait_within_foreign_thread(self, timeout):
        """
        This is the implementation of wait() when executed from a foreign (non-worker) thread.
        Here, we rely on an ordinary threading.Event primitive: ``self.finished_event``
        """
        # Don't allow this request to be cancelled, since a real thread is waiting for it.
        self.uncancellable = True

        with self._lock:
            direct_execute_needed = not self.started and (timeout is None)
            if direct_execute_needed:
                # This request hasn't been started yet
                # We can execute it directly in the current thread instead of submitting it to the request thread pool (big optimization).
                # Mark it as 'started' so that no other greenlet can claim it
                self.started = True

        if self._current_foreign_thread is not None and self._current_foreign_thread == threading.current_thread():
            # It's usually nonsense for a request to wait for itself,
            #  but we allow it if the request is already "finished"
            # (which can happen if the request is calling wait() from within a notify_finished callback)
            if self.finished:
                if self.exception is not None:
                    raise self.exception_info[0], self.exception_info[1], self.exception_info[2]
                else:
                    return
            else:
                raise Request.CircularWaitException()

        if direct_execute_needed:
            self._current_foreign_thread = threading.current_thread()
            self._execute()
        else:
            self.submit()

        # This is a non-worker thread, so just block the old-fashioned way
        completed = self.finished_event.wait(timeout)
        if not completed:
            raise Request.TimeoutException()
        
        if self.cancelled:
            # It turns out this request was already cancelled.
            raise Request.InvalidRequestException()
        
        if self.exception is not None:
            raise self.exception_info[0], self.exception_info[1], self.exception_info[2]

    def _wait_within_request(self, current_request):
        """
        This is the implementation of wait() when executed from another request.
        If we have to wait, suspend the current request instead of blocking the whole worker thread.
        """
        # Before we suspend the current request, check to see if it's been cancelled since it last blocked
        if current_request.cancelled:
            raise Request.CancellationException()

        if current_request == self:
            # It's usually nonsense for a request to wait for itself,
            #  but we allow it if the request is already "finished"
            # (which can happen if the request is calling wait() from within a notify_finished callback)
            if self.finished:
                return
            else:
                raise Request.CircularWaitException()

        with self._lock:
            # If the current request isn't cancelled but we are,
            # then the current request is trying to wait for a request (i.e. self) that was spawned elsewhere and already cancelled.
            # If they really want it, they'll have to spawn it themselves.
            if self.cancelled:
                raise Request.InvalidRequestException()
            
            if self.exception is not None:
                # This request was already started and already failed.
                # Simply raise the exception back to the current request.
                raise self.exception_info[0], self.exception_info[1], self.exception_info[2]

            direct_execute_needed = not self.started
            suspend_needed = self.started and not self.execution_complete
            if direct_execute_needed or suspend_needed:
                current_request.blocking_requests.add(self)
                self.pending_requests.add(current_request)
            
            if direct_execute_needed:
                # This request hasn't been started yet
                # We can execute it directly in the current greenlet instead of creating a new greenlet (big optimization)
                # Mark it as 'started' so that no other greenlet can claim it
                self.started = True
            elif suspend_needed:
                # This request is already started in some other greenlet.
                # We must suspend the current greenlet while we wait for this request to complete.
                # Here, we set up a callback so we'll wake up once this request is complete.
                self._sig_execution_complete.subscribe( functools.partial(current_request._handle_finished_request, self) )

        if suspend_needed:
            current_request._suspend()
        elif direct_execute_needed:
            # Optimization: Don't start a new greenlet.  Directly run this request in the current greenlet.
            self.greenlet = current_request.greenlet
            self.greenlet.owning_requests.append(self)
            self._assigned_worker = current_request._assigned_worker
            self._execute()
            self.greenlet = None
            current_request.blocking_requests.remove(self)

        if suspend_needed or direct_execute_needed:
            # No need to lock here because set.remove is atomic in CPython.
            #with self._lock:
                self.pending_requests.remove( current_request )

        # Now we're back (no longer suspended)
        # Was the current request cancelled while it was waiting for us?
        if current_request.cancelled:
            raise Request.CancellationException()
        
        # Are we back because we failed?
        if self.exception is not None:
            raise self.exception_info[0], self.exception_info[1], self.exception_info[2]

    def _handle_finished_request(self, request, *args):
        """
        Called when a request that we were waiting for has completed.
        Wake ourselves up so we can resume execution.
        """
        with self._lock:
            # We're not waiting for this one any more
            self.blocking_requests.remove(request)
            if len(self.blocking_requests) == 0:
                self._wake_up()

    def notify_finished(self, fn):
        """
        Register a callback function to be called when this request is finished.
        If we're already finished, call it now.
        
        :param fn: The callback to be notified.  Signature: fn(result)
        """
        assert not self._cleaned, "This request has been cleaned() already."
        with self._lock:
            finished = self.finished
            if not finished:
                # Call when we eventually finish
                self._sig_finished.subscribe(fn)

        if finished:
            # Call immediately
            fn(self._result)

    def notify_cancelled(self, fn):
        """
        Register a callback function to be called when this request is finished due to cancellation.
        If we're already finished and cancelled, call it now.
        
        :param fn: The callback to call if the request is cancelled.  Signature: fn()
        """
        assert not self._cleaned, "This request has been cleaned() already."
        with self._lock:
            finished = self.finished
            cancelled = self.cancelled
            if not finished:
                # Call when we eventually finish
                self._sig_cancelled.subscribe(fn)

        if finished and cancelled:
            # Call immediately
            fn()

    def notify_failed(self, fn):
        """
        Register a callback function to be called when this request is finished due to failure (an exception was raised).
        If we're already failed, call it now.

        :param fn: The callback to call if the request fails.  Signature: ``fn(exception, exception_info)``
                   exception_info is a tuple of (type, value, traceback). See Python documentation on
                   ``sys.exc_info()`` for more documentation.
        """
        assert not self._cleaned, "This request has been cleaned() already."
        with self._lock:
            finished = self.finished
            failed = self.exception is not None
            if not finished:
                # Call when we eventually finish
                self._sig_failed.subscribe(fn)

        if finished and failed:
            # Call immediately
            fn(self.exception, self.exception_info)

    def cancel(self):
        """
        Attempt to cancel this request and all requests that it spawned.
        No request will be cancelled if other non-cancelled requests are waiting for its results.
        """
        # We can only be cancelled if: 
        # (1) There are no foreign threads blocking for us (flagged via self.uncancellable) AND
        # (2) our parent request (if any) is already cancelled AND
        # (3) all requests that are pending for this one are already cancelled
        with self._lock:
            cancelled = not self.uncancellable
            cancelled &= (self.parent_request is None or self.parent_request.cancelled)
            for r in self.pending_requests:
                cancelled &= r.cancelled

            self.cancelled = cancelled
            if cancelled:
                # Any children added after this point will receive our same cancelled status
                child_requests = self.child_requests
                self.child_requests = set()

        if self.cancelled:
            # Cancel all requests that were spawned from this one.
            for child in child_requests:
                child.cancel()
    
    @classmethod
    def _current_request(cls):
        """
        Inspect the current greenlet/thread and return the request object associated with it, if any.
        """
        current_greenlet = greenlet.getcurrent()
        # Greenlets in worker threads have a monkey-patched 'owning-request' member
        if hasattr(current_greenlet, 'owning_requests'):
            return current_greenlet.owning_requests[-1]
        else:
            # There is no request associated with this greenlet.
            # It must be a regular (foreign) thread.
            return None

    @classmethod
    def current_request_is_cancelled(cls):
        """
        Return True if called from within the context of a cancelled request.
        """
        current_request = Request._current_request()
        return current_request and current_request.cancelled
    
    @classmethod
    def raise_if_cancelled(cls):
        """
        If called from the context of a cancelled request, raise a CancellationException immediately.
        """
        if Request.current_request_is_cancelled():
            raise Request.CancellationException()

    ##########################################
    #### Backwards-compatible API support ####
    ##########################################

    class _PartialWithAppendedArgs(object):
        """
        Like functools.partial, but any kwargs provided are given last when calling the target.
        """
        def __init__(self, fn, *args, **kwargs):
            self.func = fn
            self.args = args
            self.kwargs = kwargs
        
        def __call__(self, *args):
            totalargs = args + self.args
            return self.func( *totalargs, **self.kwargs)
    
    def writeInto(self, destination):
        self.fn = Request._PartialWithAppendedArgs( self.fn, destination=destination )
        return self

    def getResult(self):
        return self.result

Request.reset_thread_pool()

class RequestLock(object):
    """
    Request-aware lock.  Implements the same interface as threading.Lock.
    If acquire() is called from a normal thread, the the lock blocks the thread as usual.
    If acquire() is called from a Request, then the request is suspended so that another Request can be resumed on the thread.
    
    Requests and normal threads can *share* access to a RequestLock.
    That is, they compete equally for access to the lock.
    
    Implementation detail:  Depends on the ability to call two *private* Request methods: _suspend() and _wake_up().
    """
    logger = logging.getLogger(__name__ + ".RequestLock")
    def __init__(self):
        if Request.global_thread_pool.num_workers == 0:
            self._debug_mode_init()
        else:
            # This member holds the state of this RequestLock
            self._modelLock = threading.Lock()
    
            # This member protects the _pendingRequests set from corruption
            self._selfProtectLock = threading.Lock()
            
            # This is a list of requests that are currently waiting for the lock.
            # Other waiting threads (i.e. non-request "foreign" threads) are each listed as a single "None" item. 
            self._pendingRequests = collections.deque()

    def _debug_mode_init(self):
        """
        For debug purposes, the user can use an empty threadpool.
        In that case, all requests are executing synchronously.
        (See Request.submit().)
        In this debug mode, this class is simply a stand-in for an 
        RLock object from the builtin threading module.
        """
        # Special debugging scenario:
        # If there is no threadpool, just pretend to be an RLock
        self._debug_lock = threading.RLock()
        self.acquire = self._debug_lock.acquire
        self.release = self._debug_lock.release
        self.__enter__ = self._debug_lock.__enter__
        self.__exit__ = self._debug_lock.__exit__
        self.locked = lambda: self._debug_lock._RLock__owner is not None

    def locked(self):
        """
        Return True if lock is currently held by some thread or request.
        """
        return self._modelLock.locked()
    
    def acquire(self, blocking=True):
        """
        Acquire the lock.  If `blocking` is True, block until the lock is available.
        If `blocking` is False, don't wait and return False if the lock couldn't be acquired immediately.
        
        :param blocking: Same as in threading.Lock 
        """
        current_request = Request._current_request()
        if current_request is None:
            return self._acquire_from_within_thread(blocking)
        else:
            return self._acquire_from_within_request(current_request, blocking)

    def _acquire_from_within_request(self, current_request, blocking):
        with self._selfProtectLock:
            # Try to get it immediately.
            got_it = self._modelLock.acquire(False)
            if not blocking:
                return got_it
            if not got_it:
                # We have to wait.  Add ourselves to the list of waiters.
                self._pendingRequests.append(current_request)

        if not got_it:
            # Suspend the current request.
            # When it is woken, it owns the _modelLock.
            current_request._suspend()

            # Now we're back (no longer suspended)
            # Was the current request cancelled while it was waiting for the lock?
            if current_request.cancelled:
                raise Request.CancellationException()

        # Guaranteed to own _modelLock now (see release()).
        return True
    
    def _acquire_from_within_thread(self, blocking):
        if not blocking:
            return self._modelLock.acquire(blocking)

        with self._selfProtectLock:
            # Append "None" to indicate that a real thread is waiting (not a request)
            self._pendingRequests.append(None)

        # Wait for the internal lock to become free
        got_it = self._modelLock.acquire(blocking)
    
        with self._selfProtectLock:
            # Search for a "None" to pull off the list of pendingRequests.
            # Don't take real requests from the queue
            r = self._pendingRequests.popleft()
            while r is not None:
                self._pendingRequests.append(r)
                r = self._pendingRequests.popleft()

        return got_it

    def release(self):
        """
        Release the lock so that another request or thread can acquire it.
        """
        assert self._modelLock.locked(), "Can't release a RequestLock that isn't already acquired!"

        with self._selfProtectLock:
            if len(self._pendingRequests) == 0:
                # There were no waiting requests or threads, so the lock is free to be acquired again.
                self._modelLock.release()
            else:
                # Instead of releasing the modelLock, just wake up a request that was waiting for it.
                # He assumes that the lock is his when he wakes up.
                r = self._pendingRequests[0]
                if r is not None:
                    self._pendingRequests.popleft()
                    r._wake_up()
                else:
                    # The pending "request" is a real thread.
                    # Release the lock to wake it up (he'll remove the _pendingRequest entry)
                    self._modelLock.release()

    def __enter__(self):
        self.acquire()
        return self
    
    def __exit__(self, *args):
        self.release()

class SimpleRequestCondition(object):
    """
    A ``Request``-compatible condition variable that supports a limited
    subset of the features implemented by the standard ``threading.Condition``.

    **Limitations:**
    
    - Only one request may call :py:meth:`wait()` at a time.
    - Likewise, :py:meth:`notify()` doesn't accept the ``n`` arg.
    - Likewise, there is no ``notify_all()`` method.
    - :py:meth:`wait()` doesn't support the ``timeout`` arg.
    
    .. note:: It would be nice if we could simply use ``threading.Condition( RequestLock() )`` instead of rolling 
             our own custom condition variable class, but that doesn't quite work in cases where we need to call 
             ``wait()`` from a worker thread (a non-foreign thread).
             (``threading.Condition`` uses ``threading.Lock()`` as its 'waiter' lock, which blocks the entire worker.)

    **Example:**
    
    .. code-block:: python
        
        cond = SimpleRequestCondition()

        def process_all_data():
            with cond:
                while not all_finished:
                    while not is_data_chunk_ready():
                        cond.wait()
                    all_finished = process_available_data()

        def retrieve_some_data():
            get_some_data()
            with cond:
                cond.notify()
        
        req1 = Request( retrieve_some_data )
        req2 = Request( retrieve_some_data )
        req3 = Request( retrieve_some_data )

        req1.submit()
        req2.submit()
        req3.submit()

        # Wait for them all to finish...
        process_all_data()
        
    """
    logger = logging.getLogger(__name__ + ".SimpleRequestCondition")
    
    def __init__(self):
        if Request.global_thread_pool.num_workers == 0:
            # Special debug mode.
            self._debug_mode_init()
        else:
            self._ownership_lock = RequestLock()
            self._waiter_lock = RequestLock()   # Only one "waiter".  
                                                # Used to block the current request while we wait to be notify()ed.
        
            # Export the acquire/release methods of the ownership lock
            self.acquire = self._ownership_lock.acquire
            self.release = self._ownership_lock.release

    def _debug_mode_init(self):
        """
        For debug purposes, the user can use an empty threadpool.
        In that case, all requests are executing synchronously.
        (See Request.submit().)
        In this debug mode, this class is simply a stand-in for a 'real' 
        condition variable from the builtin threading module.
        """
        # Special debug mode initialization: 
        # Just use a normal condition variable.
        self._debug_condition = threading.Condition(threading.RLock())
        self.acquire = self._debug_condition.acquire
        self.release = self._debug_condition.release
        self.wait = self._debug_condition.wait
        self.notify = self._debug_condition.notify
        
        self._ownership_lock = self._debug_condition
        #self.__enter__ = self._debug_condition.__enter__
        #self.__exit__ = self._debug_condition.__exit__        

    def __enter__(self):
        self._ownership_lock.__enter__()
        
    def __exit__(self, *args):
        self._ownership_lock.__exit__(*args)

    def wait(self):
        """
        Wait for another request to call py:meth:``notify()``.  
        The caller **must** own (acquire) the condition before calling this method.
        The condition is automatically ``released()`` while this method waits for 
        ``notify()`` to be called, and automatically ``acquired()`` again before returning.

        .. note:: Unlike ``threading.Condition``, it is **NOT** valid to call ``wait()`` 
                  from multiple requests in parallel.  That is, this class supports only 
                  one 'consumer' thread.

        .. note:: Unlike ``threading.Condition``, no ``timeout`` parameter is accepted here.
        """
        # Should start out in the non-waiting state
        assert not self._waiter_lock.locked()
        self._waiter_lock.acquire()
        
        # Temporarily release the ownership lock while we wait for someone to release the waiter.
        self._ownership_lock.release()
        
        # Try to acquire the lock AGAIN.
        # This isn't possible until someone releases it via notify()
        # (Note that RequestLock does NOT have RLock semantics.)
        self._waiter_lock.acquire()
        
        # Re-acquire
        self._ownership_lock.acquire()

        # Reset for next wait()
        # Must check release status here in case someone called notify() in between the previous two lines
        if self._waiter_lock.locked():
            self._waiter_lock.release()

    def notify(self):
        """
        Notify the condition that it can stop ``wait()``-ing.
        The called **must** own (acquire) the condition before calling this method.
        Also, the waiting request cannot return from ``wait()`` until the condition is released, 
        so the caller should generally release the condition shortly after calling this method.
        
        .. note:: It is okay to call this from more than one request in parallel.
        """
        # Release the waiter for anyone currently waiting
        if self._waiter_lock.locked():
            self._waiter_lock.release()


class RequestPool(object):
    """
    Convenience class for submitting a batch of requests and waiting until they are all complete.
    Requests can not be added to the pool after it has already started.
    Not threadsafe (don't add requests from more than one thread).
    """

    class RequestPoolError(Exception):
        """
        Raised if you attempt to use the Pool in a manner that it isn't designed for.
        """
        pass

    def __init__(self):
        self._requests = set()
        self._finishing_requests = set()
        self._started = False

    def __len__(self):
        return len(self._requests)

    def add(self, req):
        """
        Add a request to the pool.  The pool must not be submitted yet.  Otherwise, an exception is raised.
        """
        if self._started:
            # For now, we forbid this because it would allow some corner cases that we aren't unit-testing yet.
            # If this exception blocks a desirable use case, then change this behavior and provide a unit test.
            raise RequestPool.RequestPoolError("Attempted to add a request to a pool that was already started!")
        def remove_request(*args):
            # This request is done executing, but not quite finished with its callbacks.
            # See docstring in wait() for details.
            self._finishing_requests.add(req)
            try:
                self._requests.remove(req)
            except KeyError:
                # request may have been removed already by the while
                # loop in the wait() method
                pass

        req.notify_finished(remove_request)
        req.notify_failed(remove_request)
        req.notify_cancelled(remove_request)
        self._requests.add(req)

    def submit(self):
        """
        Submit all the requests in the pool.  The pool must not be submitted yet.  Otherwise, an exception is raised.
        """
        if self._started:
            raise RequestPool.RequestPoolError("Can't re-start a RequestPool that was already started.")
        # shallow copy prevents python complaining when finished requests
        # remove themselves from self._requests
        requests = self._requests.copy()
        # while loop with pop() allows the gc to clean up completed requests
        while requests:
            requests.pop().submit()

    def wait(self):
        """
        If the pool hasn't been submitted yet, submit it. Then wait for all requests in the pool to complete.
        
        To be efficient with memory, we attempt to discard requests quickly after they complete.
        To achieve this, we keep requests in two sets:
        
        _requests: All requests that are still executing or 'finishing'
        _finishing_requests: Requests whose main work has completed, but may still be executing callbacks
                             (e.g. handlers for notify_finished)

        Requests are transferred from the first set to the second as they complete.
        
        We try to block() for 'finishing' requests first, so they can be discarded quickly.
        (If we didn't block for 'finishing' requests at all, we'd be violating the Request 'Callback Timing Guarantee', 
        which must hold for both Requests and RequestPools.  See Request docs for details.)        
        """
        def _clear_finishing_requests():
            while self._finishing_requests:
                try:
                    req = self._finishing_requests.pop()
                except KeyError:
                    break
                else:
                    req.block()            
        
        if not self._started:
            self.submit()
        
        while self._requests:
            # First, clear the queue of 'finishing' requests.
            # We want to discard them as soon as possible.
            _clear_finishing_requests()
            
            # Next, wait for the next non-'finishing' request.
            try:
                req = self._requests.pop()
            except KeyError:
                # the _requests set was modified in the mean time
                # we can quit the loop since there are no more requests
                break
            else:
                req.block()

        # Finally, now all the requests are either 'finishing' or totally complete.
        # DON'T EXIT until all requests are totally complete. 
        #  (Once their callbacks have completed, they are no longer 'finishing')
        _clear_finishing_requests()

    def cancel(self):
        """
        Cancel all requests in the pool.
        """
        for req in self._requests:
            req.cancel()
    
    def request(self, func):
        """
        **Deprecated method**.  Convenience function to construct a request for the given callable and add it to the pool.
        """
        self.add( Request(func) )
    
    def clean(self):
        """
        Release our handles to all requests in the pool, for cleanup purposes.
        """
        self._requests = set()


class RequestPool_SIMPLE(object):
    # This simplified version doesn't attempt to be efficient with RAM like the standard version (above).
    # It is provided here as a simple reference implementation for comparison and testing purposes.
    """
    Convenience class for submitting a batch of requests and waiting until they are all complete.
    Requests can not be added to the pool after it has already started.
    Not threadsafe (don't add requests from more than one thread).
    """

    logger = logging.getLogger(__name__ + ".RequestPool")

    def __init__(self):
        self._requests = set()
        self._started = False

    def __len__(self):
        return len(self._requests)

    def add(self, req):
        """
        Add a request to the pool.  The pool must not be submitted yet.  Otherwise, an exception is raised.
        """
        if self._started:
            # For now, we forbid this because it would allow some corner cases that we aren't unit-testing yet.
            # If this exception blocks a desirable use case, then change this behavior and provide a unit test.
            raise RequestPool.RequestPoolError("Attempted to add a request to a pool that was already started!")
        self._requests.add(req)

    def submit(self):
        """
        Submit all the requests in the pool.  The pool must not be submitted yet.  Otherwise, an exception is raised.
        """
        if self._started:
            raise RequestPool.RequestPoolError("Can't re-start a RequestPool that was already started.")

        for req in self._requests:
            req.submit()

    def wait(self):
        """
        If the pool hasn't been submitted yet, submit it. 
        Then wait for all requests in the pool to complete in the simplest way possible.
        """
        if not self._started:
            self.submit()

        for req in self._requests:
            req.block()

    def cancel(self):
        """
        Cancel all requests in the pool.
        """
        for req in self._requests:
            req.cancel()
    
    def request(self, func):
        """
        **Deprecated method**.  Convenience function to construct a request for the given callable and add it to the pool.
        """
        self.add( Request(func) )
    
    def clean(self):
        """
        Release our handles to all requests in the pool, for cleanup purposes.
        """
        self._requests = set()

