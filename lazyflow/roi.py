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
if __name__ == "__main__":
    # When executing this file directly for doctest purposes,
    #  we must remove the lazyflow module from sys.path
    # This is due to the fact that the builtin collections module 
    #  ALSO has an 'operator' module,
    #  which conflicts with lazyflow.operator in this case.    
    import sys
    assert sys.path[0].endswith('lazyflow/lazyflow')
    sys.path.pop(0)

import numpy
from numpy.lib.stride_tricks import as_strided as ast
from math import ceil, floor, pow, log10
import collections

class TinyVector(list):
    __slots__ = []

    def copy(self):
        return TinyVector(self)

    def __add__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x+y for x,y in zip(self,other))
        else:
            return TinyVector(x+other for x in self)

    __radd__ = __add__

    def __iadd__(self, other):
        # Must explicitly override list.__iadd__
        # Others (e.g. isub, imul) can use default implementation.
        if isinstance(other, collections.Iterable):
            self =  TinyVector(x+y for x,y in zip(self,other))
            return self
        else:
            self = TinyVector(x+other for x in self)
            return self

    def __sub__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x-y for x,y in zip(self,other))
        else:
            return TinyVector(x-other for x in self)

    def __rsub__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(y-x for x,y in zip(self,other))
        else:
            return TinyVector(other-x for x in self)

    def __mul__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x*y for x,y in zip(self,other))
        else:
            return TinyVector(x*other for x in self)

    __rmul__ = __mul__

    def __div__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x/y for x,y in zip(self,other))
        else:
            return TinyVector(x/other for x in self)

    def __rdiv__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(y/x for x,y in zip(self,other))
        else:
            return TinyVector(other/x for x in self)

    def __mod__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x%y for x,y in zip(self,other))
        else:
            return TinyVector(x%other for x in self)

    def __rmod__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(y%x for x,y in zip(self,other))
        else:
            return TinyVector(other%x for x in self)

    def __floordiv__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x//y for x,y in zip(self,other))
        else:
            return TinyVector(x//other for x in self)

    def __rfloordiv__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(y//x for x,y in zip(self,other))
        else:
            return TinyVector(other//x for x in self)

    def __eq__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x==y for x,y in zip(self,other))
        else:
            return TinyVector(x==other for x in self)

    def __ne__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x!=y for x,y in zip(self,other))
        else:
            return TinyVector(x!=other for x in self)

    def __ge__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x>=y for x,y in zip(self,other))
        else:
            return TinyVector(x>=other for x in self)

    def __le__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x<=y for x,y in zip(self,other))
        else:
            return TinyVector(x<=other for x in self)

    def __gt__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x>y for x,y in zip(self,other))
        else:
            return TinyVector(x>other for x in self)

    def __lt__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x<y for x,y in zip(self,other))
        else:
            return TinyVector(x<other for x in self)

    def __and__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x&y for x,y in zip(self,other))
        else:
            return TinyVector(x&other for x in self)

    __rand__ = __and__

    def __or__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x|y for x,y in zip(self,other))
        else:
            return TinyVector(x|other for x in self)

    __ror__ = __or__

    def __xor__(self, other):
        if isinstance(other, collections.Iterable):
            return TinyVector(x^y for x,y in zip(self,other))
        else:
            return TinyVector(x ^ other for x in self)

    __rxor__ = __xor__
    
    def __neg__(self):
        return TinyVector( -x for x in self )
    
    def __abs__(self):
        return TinyVector( map(abs, self) )

    def __pos__(self):
        return TinyVector(self)
    
    def __invert__(self):
        return TinyVector(~x for x in self)
    
    def ceil(self):
        return TinyVector(map(ceil ,self))
        #return numpy.ceil(numpy.array(self))

    def floor(self):
        return TinyVector(map(floor ,self))
        #return numpy.floor(numpy.array(self))

    def _asint(self):
        return TinyVector(map(lambda x:  x.__int__() ,self))

    def insert(self,index, value):
        l = list(self)
        l.insert(index,value)
        return TinyVector(l)

    def all(self):
        answer = True
        for e in self:
            if not e:
                answer = False
                break
        return answer

    def any(self):
        answer = False
        for e in self:
            if e:
                answer = True
                break
        return answer

def expandSlicing(s, shape):
    """
    Args:
        s: Anything that can be used as a numpy array index:
           - int
           - slice
           - Ellipsis (i.e. ...)
           - Some combo of the above as a tuple or list
        
        shape: The shape of the array that will be accessed
        
    Returns:
        A tuple of length N where N=len(shape)
        slice(None) is inserted in missing positions so as not to change the meaning of the slicing.
        e.g. if shape=(1,2,3,4,5):
            0 --> (0,:,:,:,:)
            (0:1) --> (0:1,:,:,:,:)
            : --> (:,:,:,:,:)
            ... --> (:,:,:,:,:)
            (0,0,...,4) --> (0,0,:,:,4)            
    """
    if type(s) == list:
        s = tuple(s)
    if type(s) != tuple:
        # Convert : to (:,), or 5 to (5,)
        s = (s,)

    # Compute number of axes missing from the slicing
    if len(shape) - len(s) < 0:
        assert s == (Ellipsis,) or s == (slice(None),), \
            "Slicing must not have more elements than the shape, except for [:] and [...] slices.\n"\
            "Your slicing: {}, your shape: {}".format( s, shape )            

    # Replace Ellipsis with (:,:,:)
    if Ellipsis in s:
        ei = s.index(Ellipsis) # Ellipsis Index
        s = s[0:ei] + (len(shape) - len(s) + 1)*(slice(None),) + s[ei+1:]

    # Append (:,) until we get the right length
    s += (len(shape) - len(s))*(slice(None),)
    
    # Special case: we allow [:] and [...] for empty shapes ()
    if shape == ():
        s = ()
    
    return s

sTrl1 =   lambda x: x if type(x) != slice else x.start if x.start != None else 0
sTrl2 =  lambda x,y: y if type(y) != slice else y.stop if y.stop != None else x
sTrl3 = lambda x,y: y + 1 if x == y else y
def sliceToRoi(s, shape, extendSingleton = True):
    """Args:
            slice: slice object (1D) or list of slice objects (N-D)
            shape: the shape of the array to be sliced
            extendSingleton: if True, convert int indexes to slices so the dimension of the slicing matches the dimension of the shape.
       Returns:
            ROI instance corresponding to slice
    """
    s = expandSlicing(s, shape)
    start = map(sTrl1, s)
    stop = map(sTrl2, shape,s)
    if extendSingleton:
        stop = map(sTrl3,start,stop)
    return TinyVector(start), TinyVector(stop)

def roiFromShape(shape):
    start = TinyVector([0] * len(shape))
    stop = TinyVector(shape)
    return ( start, stop )

def fullSlicing(shape):
    return roiToSlice(*roiFromShape(shape))

def getIntersection( roiA, roiB, assertIntersect=True ):    
    start = numpy.maximum( roiA[0], roiB[0] )    
    stop = numpy.minimum( roiA[1], roiB[1] )

    if ((stop - start) <= 0).any():
        if assertIntersect:
            assert ((stop - start) > 0).all(), "Rois do not intersect: {} and {}".format( roiA, roiB )
        else:
            return None    
    return (start, stop)

rTsl1 = lambda x,y:slice(x.__int__(),y.__int__())
def roiToSlice(start, stop, hardBind=False):
    """Args:
            start (N-D coordinate): inclusive start
            stop  (N-D coordinate): exclusive stop
       Returns:
            list of slice objects describing the [start,stop) range,
            so that numpy.ndarray.__getitem__(roiToSlice(start,stop)) can be used.
    """
    if hardBind:
        res = []
        for sta, stp in zip(start,stop):
            if stp == sta + 1 or stp == sta:
                res.append(int(sta))
            else:
                res.append(slice(int(sta),int(stp)))
        return tuple(res)
    else:
        return tuple(map(rTsl1,start,stop))


def extendSlice(start, stop, shape, sigma, window = 3.5):
    # TODO: Rename this function, since it doesn't use slice() objects.
    zeros = start - start
    if isinstance( sigma, collections.Iterable ):
        sigma = TinyVector(sigma)
    if isinstance( start, collections.Iterable ):
        ret_type = type(start[0])
    else:
        ret_type = type(start)
    newStart = numpy.maximum(start - numpy.ceil(window * sigma), zeros).astype( ret_type )
    sa = numpy.array(shape)
    newStop = numpy.minimum(stop + numpy.ceil(window * sigma), sa).astype( ret_type )
    return newStart, newStop


def block_view(A, block= (3, 3)):
    """Provide a 2D block view to 2D array. No error checking made.
    Therefore meaningful (as implemented) only for blocks strictly
    compatible with the shape of A."""
    # simple shape and strides computations may seem at first strange
    # unless one is able to recognize the 'tuple additions' involved ;-)
    shape= (A.shape[0]/ block[0], A.shape[1]/ block[1])+ block
    strides= (block[0]* A.strides[0], block[1]* A.strides[1])+ A.strides
    return ast(A, shape= shape, strides= strides)

def getIntersectingBlocks( blockshape, roi, asarray=False ):
    """
    Returns the start coordinate of each block that the given roi intersects.
    By default, returned as an array of shape (N,M) (N indexes with M coordinates each).
    If asarray=True, then the blocks are returned as an array of shape (D1,D2,D3,...DN,M)
    such that coordinates of spatially adjacent blocks are returned in adjacent entries of the array.

    For example:

    >>> block_starts = getIntersectingBlocks( (10, 20), [(15, 25),(23, 40)] )
    >>> block_starts.shape
    (2, 2)
    >>> print block_starts
    [[10 20]
     [20 20]]

    >>> block_starts = getIntersectingBlocks( (10, 20), [(15, 25),(23, 41)] )
    >>> block_starts.shape
    (4, 2)
    >>> print block_starts
    [[10 20]
     [10 40]
     [20 20]
     [20 40]]

    Now the same two examples, with asarray=True.  Note the shape of the result.
    
    >>> block_start_matrix = getIntersectingBlocks( (10, 20), [(15, 25),(23, 40)], asarray=True )
    >>> block_start_matrix.shape
    (2, 1, 2)
    >>> print block_start_matrix
    [[[10 20]]
    <BLANKLINE>
     [[20 20]]]

    >>> block_start_matrix = getIntersectingBlocks( (10, 20), [(15, 25),(23, 41)], asarray=True )
    >>> block_start_matrix.shape
    (2, 2, 2)
    >>> print block_start_matrix
    [[[10 20]
      [10 40]]
    <BLANKLINE>
     [[20 20]
      [20 40]]]
 

    This function works for negative rois, too.
    
    >>> block_starts = getIntersectingBlocks( (10, 20), [(-10, -5),(5, 5)] )
    >>> print block_starts 
    [[-10 -20]
     [-10   0]
     [  0 -20]
     [  0   0]]
    """
    assert len(blockshape) == len(roi[0]) == len(roi[1]), "blockshape and roi are mismatched."
    roistart = TinyVector( roi[0] )
    roistop = TinyVector( roi[1] )
    blockshape = TinyVector( blockshape )
    
    block_index_map_start = roistart / blockshape
    block_index_map_stop = ( roistop + (blockshape - 1) ) / blockshape # Add (blockshape-1) first as a faster alternative to ceil() 
    block_index_map_shape = block_index_map_stop - block_index_map_start
    
    num_axes = len(blockshape)
    block_indices = numpy.indices( block_index_map_shape )
    block_indices = numpy.rollaxis( block_indices, 0, num_axes+1 )
    block_indices += block_index_map_start

    # Multiply by blockshape to get the list of start coordinates
    block_indices *= blockshape

    if asarray:
        return block_indices
    else:
        # Reshape into N*M matrix for easy iteration
        num_indexes = numpy.prod(block_indices.shape[0:-1])
        axiscount = block_indices.shape[-1]
        return numpy.reshape( block_indices, (num_indexes, axiscount) )

def getBlockBounds(dataset_shape, block_shape, block_start):
    """
    Given a block start coordinate and block shape, return a roi for 
    the whole block, clipped to fit within the given dataset shape.
    
    >>> getBlockBounds( [35,35,35], [10,10,10], [10,20,30] )
    (array([10, 20, 30]), array([20, 30, 35]))
    """
    assert (numpy.mod( block_start, block_shape ) == 0).all(), "Invalid block_start.  Must be a multiple of the block shape!"

    entire_dataset_roi = roiFromShape( dataset_shape )
    block_shape = TinyVector( block_shape )
    block_bounds = ( block_start, block_start + block_shape )
    
    # Clip to dataset bounds
    block_bounds = getIntersection( block_bounds, entire_dataset_roi )
    return block_bounds

def determineBlockShape( max_shape, target_block_volume ):
    """
    Choose a blockshape that is close to the target_block_volume (in pixels), 
    without exceeding max_shape in any dimension.
    
    The resulting blockshape will be as isometric as possible, except 
    for those dimensions must be restricted due to small max_shape.  If possible, the 
    block's other dimensions are expanded to achieve a total volume of target_block_volume.
    
    >>> determineBlockShape( (1000,2000,3000,1), 1e6 )
    (100, 100, 100, 1)

    >>> determineBlockShape( (1,100,5,200,3), 1000 )
    (1, 8, 5, 8, 3)
    """
    assert (TinyVector(max_shape) > 0).all(), "Invalid max_shape: {}".format( max_shape )
    ndims = len( max_shape )
    
    # Attach indexes to remember where each max_shape element came from
    max_with_index = zip( max_shape, range( len(max_shape) ) )
    
    # Sort from smallest to largest, to ensure that larger dimensions
    #   will pick up the slack that smaller dims can't accommodate.
    sorted_max = sorted( max_with_index )
    
    volume_so_far = 1
    block_shape = []
    
    for (m, i), num_remaining_axes in zip(sorted_max, range(ndims, 0, -1)):
        # Make a block_shape that is isometric in the remaining dimensions
        remaining_factor = target_block_volume/volume_so_far
        block_side = int( pow( remaining_factor, 1.0/num_remaining_axes ) + 0.5 )
        block_side = min( block_side, m )
        block_shape.append( block_side )
        volume_so_far *= block_side        
    
    # Sort block_shape dimensions back to the original axis order
    index_order = zip( *sorted_max )[1]
    indexed_block_shape = zip( index_order, block_shape )
    block_shape = zip( *sorted( indexed_block_shape ) )[1]    
    return tuple(block_shape)

def determine_optimal_request_blockshape( max_blockshape, ideal_blockshape, ram_usage_per_requested_pixel, num_threads, available_ram ):
    """
    Choose a blockshape for requests subject to the following constraints:
    - not larger than max_blockshape in any dimension
    - not too large to run in parallel without exceeding available ram (according to num_threads and available_ram)
    
    Within those constraints, choose the largest blockshape possible.  
    The blockshape will be chosen according to the following heuristics:
    - If any dimensions in ideal_blockshape are 0, prefer to expand those first until max_blockshape is reached.
    (The result is known as atomic_blockshape.)
    - After that, attempt to expand the blockshape by incrementing a dimension according to its width in atomic_blockshape.
    
    Note: For most use-cases, the ``ram_usage_per_requested_pixel`` parameter refers to the ram consumed when requesting ALL channels of an image.
          Therefore, you probably want to omit the channel dimension from your max_blockshape and ideal_blockshape parameters.
    
    >>> determine_optimal_request_blockshape( (1000,1000,100), (0,0,1), 4, 10, 1e6 )
    (158, 158, 1)
    
    >>> determine_optimal_request_blockshape( (1000,1000,100), (0,0,1), 4, 10, 1e9 )
    (1000, 1000, 24)
    
    """
    assert len( max_blockshape ) == len( ideal_blockshape )
    
    # Convert to numpy for convenience.
    max_blockshape = numpy.asarray( max_blockshape )
    ideal_blockshape = numpy.asarray( ideal_blockshape )

    target_block_volume_bytes = available_ram / num_threads
    target_block_volume_pixels = target_block_volume_bytes / ram_usage_per_requested_pixel
    
    # Replace 0's in the ideal_blockshape with the corresponding piece of max_blockshape
    complete_ideal_blockshape = numpy.where( ideal_blockshape == 0, max_blockshape, ideal_blockshape )
    
    # Clip to max
    clipped_ideal_blockshape = numpy.minimum( max_blockshape, complete_ideal_blockshape )

    # Try to expand.
    atomic_blockshape = determineBlockShape( clipped_ideal_blockshape, target_block_volume_pixels )
    atomic_blockshape = numpy.asarray( atomic_blockshape )
    
    # Does our blockshape consume all available RAM?  Or was it limited by the max_shape?
    blockshape = atomic_blockshape
    while True:
        # Find a dimension of atomic_blockshape that isn't already maxed out,
        # And see if we have enough RAM to 
        for index in range( len(blockshape) ):
            # If we were to expand the blockshape in this dimension, would the block still fit in RAM?
            candidate_blockshape = blockshape.copy()
            candidate_blockshape[index] += atomic_blockshape[index]
            if (candidate_blockshape <= max_blockshape).all() and \
               (numpy.prod(candidate_blockshape) < target_block_volume_pixels):
                blockshape = candidate_blockshape
                break
        if blockshape is not candidate_blockshape:
            break
        
    return tuple(blockshape)

def slicing_to_string( slicing, max_shape=None ):
    """
    Returns a string representation of the given slicing, which has been 
    formatted with spaces so that multiple such slicings could be printed 
    in rows with their columns lined up.
    
    slicing: A tuple or slice objects, e.g. as returned by numpy.s_
    max_shape: If provided, used to determine how many columns to use 
               for each slicing field.
    """
    if max_shape:
        max_digits = map(lambda s: int(log10(s))+1, max_shape)
    else:
        max_digits = [0] * len(slicing)
    slice_strings = []
    for i, sl in enumerate(slicing):
        s = ""
        s += ("{:" + str(max_digits[i]) + "}").format( sl.start )
        s += " : "
        s += ("{:" + str(max_digits[i]) + "}").format( sl.stop )
        assert sl.step is None
        slice_strings.append(s)
    return "(" + ",  ".join( slice_strings ) + ")"

if __name__ == "__main__":
    import doctest
    doctest.testmod()
