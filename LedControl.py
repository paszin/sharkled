import threading
from threading import Thread
import time # for timestamp
from functools import partial
import sys 
import logging
import random
import math
import colorsys # for converting to hsv
import collections
import re #for parsing areas
import inspect

from constants import *


if sys.platform in ['linux2', 'linux']:
	## using raspberry
	RPI = True
	import unicornhat as ws
else:
	from wsscreen import Stripe 
	ws = Stripe() 
	RPI = False
	t = Thread(target=ws.win.mainloop, args=())
	#t.start()


#logging.basicConfig(filename='ledcontrol.log',level=logging.DEBUG)

class LEDMaster:

	def __init__(self):
		
		self.framerate = 30 #ms
		self.actualframerate = 30 #ms
		self.controllers = collections.OrderedDict()
		self.finish = False
		self.bufferThread = Thread(target=self.writeBuffer)
		if RPI:
			self.bufferThread.start()

	def add(self, name, parameters={}):
		'''adds or updates the controller indentified by name 
		'''
		compare = lambda x, y: collections.Counter(x) == collections.Counter(y)
		id = -1
		for i, controller in self.controllers.iteritems(): # update controller if areas are the same
			if controller.name == name and compare(parameters.get('areas'), controller.parameters.get('areas')):
				controller.parameters = parameters
				id = controller.id
				#logging.debug("updated controller %s" %str(self.controllers))
		if id < 0: # if no matches: create new controller
			c = LEDEffect(name, parameters)
			self.controllers[c.id] = c
			id = c.id
			#logging.debug("added new controller %s" %str(self.controllers))
		## order dict
		self.controllers = collections.OrderedDict(sorted(self.controllers.items(), key=lambda (i, c): (c.parameters.get('z'), c.id)))
		return id

	def getController(self, cid):
		''' returns an instance of LEDController'''
		return self.controllers[cid]

	def getControllerParameters(self, cid):
		'''return dictionary with corresponding parameters'''
		return self.controllers[cid].parameters

	def reset(self):
		''' deletes all controllers and set everything to black'''
		self.controllers = OrderedDict()
		self.clear()

	def clear(self):
		'''set everything to black'''
		self.buffer = LEDS_COUNT*[(0, 0, 0, 1)]

	def finishControllerById(self, cid):
		'''removes the corresponding controller'''
		del self.controllers[cid]

	def getLeds(self):
		'''returns 3-tupels of the current state of the leds'''
		out = []
		for i in range(LEDS_COUNT):
			out.append(ws.get_pixel(i))
		return out

	@staticmethod
	def getEffects():
		'''returns a list with all available effects including parameters with default values'''
		out = []
		methods = inspect.getmembers(LEDEffect)
		for name, func in methods:
			if not name.startswith('_') and inspect.ismethod(func):
				parameters = inspect.getargspec(func)
				if parameters.defaults and len(parameters.args)-3 == len(parameters.defaults):
					## first two parameters are ts and pos and self, all the others have default values
					pout = dict(zip(parameters.args[3:], parameters.defaults))
				else:
					pount = {}
				out.append({'name': name, 'parameters': pout})
		return out

	@staticmethod
	def getDefaultParameters(effect):
		'''return all the default parameters for the given effect name'''
		out = []
		methods = inspect.getmembers(LEDEffect)
		for name, func in methods:
			if name == effect:
				parameters = inspect.getargspec(func)
				if parameters.defaults and len(parameters.args)-3 == len(parameters.defaults):
					## first two parameters are ts and pos and self, all the others have default values
					return dict(zip(parameters.args[3:], parameters.defaults))	
		return {}


	@staticmethod
	def getTimestamp():
		'''returns milliseconds as integer'''
		return int(time.time()*1000)

	def getTimestamp(self):
		'''returns milliseconds as integer'''
		return int(time.time()*1000)
				
	def writeBuffer(self):
		'''this function is running in an extra thread
		execute the effect function of the LEDControllers and set the pixels
		'''
		mixInto = lambda base, mix: map(lambda (b, m) : b*(1-mix[3])+m*mix[3], zip(base, mix)) ## interpolate with alpha value of mix
		self.buffer = LEDS_COUNT*[[0,0,0]]
		while True:
			if self.finish:
				break
			timestamp = self.getTimestamp()
			controllers = self.controllers.values() #because of thread problems fetching before iterating is important
			for controller in controllers:
				if controller.paused: continue
				buffers = controller._effect(timestamp + controller.offset)
				for (buffer, pos) in buffers:
					for i, pos in enumerate(pos):
						if buffer[i][3] == 0: continue
						elif buffer[i][3] == 1: self.buffer[pos] = buffer[i]
						else: self.buffer[pos] = mixInto(self.buffer[pos], buffer[i])		
			skip = 0
			for i, c in enumerate(self.buffer):
				if i in DEFECTLEDS:
					skip += 1
				else:
					ws.set_pixel(i - skip, int(255*c[0]), int(255*c[1]), int(255*c[2]))
			ws.show()
			timestampNow = self.getTimestamp()
			wait = (self.actualframerate - (timestampNow - timestamp))
			if wait > 0:
				time.sleep(wait / 1000.0)
				if wait > 10: 
					self.actualframerate = max(self.actualframerate - 10,self.framerate)
			else:
				self.actualframerate += 10
			
class LEDController:
	'''stores all default functions'''
	id = 0
	def __init__(self, name, parameters):
		self.name = name
		LEDController.id += 1
		self.paused = False
		self._updateParameters(parameters)

	def __repr__(self):
		return "LEDController " + self.name + '<' + str(len(self.pos)) + '>'

	def __str__(self):
		return "LEDController " + self.name + '<' + str(len(self.pos)) + '>'


	def _updateParameters(self, parameters):
		self.parameters = parameters
		self.areas = parameters.get('areas') or ['all']
		self.pos = self._resolve(self.areas)
		self.offset = parameters.get('offset') or 0
		if self.offset == '-1': self.offset = - int(time.time()*1000)

	def _resolve(self, areas):
		'''...'''
		pos = []
		for a in areas:
			matches = re.search(r'([^\[]*)(\[.+\])?', a).groups() # split between ':'
			resolved = AREAS[matches[0]]
			if type(resolved[0]) == str:
				index = (matches[1] or '')[1:-1]
				indexes = index.split(':')
				cleanIndexes = [None] * 3
				for i in range(len(indexes)):
					cleanIndexes[i] = int(indexes[i]) if indexes[i] != '' else None
				pos += self._resolve(resolved)[cleanIndexes[0]:cleanIndexes[1]:cleanIndexes[2]]
			else:
				pos.append(resolved)
		return pos

	
	def _effect(self, ts):
		mergeType = self.parameters.get('mergeType') or 'concat'
		pos = []
		if mergeType == 'concat':
			for array in self.pos:
				pos += array
			return [(getattr(self, self.name)(ts, pos, **self.parameters), pos)]	
		elif mergeType == 'syncro':
			buffers = []
			for array in self.pos:
				buffers.append((getattr(self, self.name)(ts, array, **self.parameters), array))
			return buffers

	def _mixInto(self, base, mix):
		if len(base) < 4: base.append(1)
		if len(mix) < 4: mix.append(1) 
		if base[3] == 0: return mix
		if mix[3] == 0: return base
		return map(lambda (b, m): b*base[3]*(1-mix[3])+m*mix[3], zip(base, mix)) ## interpolate with alpha value of mix

	def _interpolate(self, color1, color2):
		return [self._mixInto(c1, c2) for c1, c2 in zip(color1, color2)]


class LEDEffect(LEDController):

	def color(self, ts, pos, color=[1,1,1,1], **kwargs):
		'''Description: set a solid color
		Parameters: 
			color | 4-tupel of floats between 0..1 
		'''
		return len(pos) * [color]

	def sequence(self, ts, pos, interval=1000, sequence=[[1,0,0,1],[0,1,0,1],[0,0,1,1]], fadespeed=0, **kwargs):
		'''Description: fades from color to color.
		Parameters:
			interval:
			sequence:
			fadespeed:
		'''
		sinceLast = ts % interval
		step = (ts % (interval*len(sequence))) / interval
		color = sequence[step]
		if fadespeed > 0:
			fadeCorrection = (fadespeed - sinceLast) / float(fadespeed)
			if fadeCorrection >= 0 and fadeCorrection <= 1:
				for i in range(4):
					color[i] = (fadeCorrection * sequence[step-1][i] + (1 - fadeCorrection) * sequence[step][i])
		return len(pos) * [color]		

	def chase(self, ts, pos, interval=500, count=1, width=5, soft=0, color=[1, 1, 1, 1], background=[0,0,0,1], **kwargs):
		'''Description: generates a chase effect
		Parameters:
			interval:
			count: 
			width:
			soft:
			color:
			background:
			 '''
		length = len(pos)
		position = int(length / (interval / float((ts % interval) + 1)))
		buffer = [background] * length
		for i in range(-soft, width + soft):
			if i < 0:
				alpha = 1 + (i / float(soft))
			elif i >= width:
				alpha = 1 - ((i-width) / float(soft))
			else:
				alpha = 1
			for j in range(0, length, length / count):
				buffer[(position + i + j) % length] = \
					self._mixInto(buffer[(position + i + j) % length], color[:3] + [(color[3] + alpha) / 2])
		return buffer

	def bucketColor(self, ts, pos, interval=1000, colors=[[1,0,0,1],[0,1,0,1],[0,0,1,1],[0,0,0,1]], bucketsize=1, **kwargs):
		'''Description:
		Parameters:
			interval:
			color:
			bucketsize:
		'''
		length = len(pos)
		if not hasattr(self, 'lastTs') or self.lastTs + interval < ts:
			colormap = []
			for bucket in range((length/bucketsize) + 1):
				bucketcolor = random.choice(colors)
				for i in range(bucketsize):
					if i < length:
						colormap.append(bucketcolor)
			self.lastTs = ts
			self.colormap = colormap
		return self.colormap

	def rainbow(self, ts, pos, interval=1000, wavelength=100, alpha=1, **kwargs):
		'''Description: generates a rainbow
		Parameters: 
			interval:
			wavelength:
			alpha: float | 0..1 | transparency of rainbow
			'''
		length = len(pos)
		relativeInterval = ((ts % interval) / float(interval))
		colormap = []
		for i in range(length):
			pos = (i / float(wavelength))
			colormap.append(list(colorsys.hsv_to_rgb(pos + relativeInterval, 1.0, 1.0))+[alpha])
		return colormap

	def pulsate(self, ts, pos, interval=1000, wavelength=100, color=[1,1,1,1], background=[0,0,0,0], **kwargs):
		'''Description: generates a pulsating light
		Parameters:
			interval:
			wavelength:
			color:
			background:
		'''
		length = len(pos)
		relativeInterval = ((ts % interval) / float(interval))
		buffer = []
		for i in range(length):
			pos = (i / float(wavelength))
			alpha = 0.5 + 0.5*math.sin((pos + relativeInterval - 0.25) * 2 * math.pi)
			buffer.append(self._mixInto(background, color[:3] + [(color[3] + alpha) / 2]))
		return buffer


	def christmas(self, ts, pos, **kwargs):
		color1 = self.color(ts, pos, color=[1, 0.1, 0.25, 1])
		color2 = self.rainbow(ts, pos, alpha=0.4, interval=10000)
		colormix = self._interpolate(color1, color2)
		for i in range(len(pos)):
			if random.random() > 0.99995:
				for j in range(i-5, i):
					colormix[j] = [1, 1, 1, 1]
		return colormix
		
if __name__ == '__main__':


	master = LEDMaster()
	print LEDMaster.getEffects()

	
	master.add(name='christmas')
	master.add(name='chase', parameters={'count': 4, 'areas': ['all'], 'interval': 60000,  'color': [0,0,1,0.8], 'soft': 20, 'width': 1, 'background': [0,0,0,0]})
	wait = raw_input("Enter to finish")
	master.finish = True

	#id1 = master.add(name='bucketColor', parameters = {'areas': ['Wand']})
	master.add(name='color', parameters = {'areas': ['all'], 'color': [1, 0.1, 0.25, 1]})
	master.add(name='rainbow', parameters = {'areas': ['all'], 'alpha': 0.4, 'interval': 10000})
	#wait = raw_input("Enter to finish")
	master.reset()
	#time.sleep(3)
	
	
	
	# for i in range(300):
	# 	ws.set_pixel(i, 200, 0, 50)
	# 	ws.set_pixel(i+1, 100, 0, 50)
	# 	ws.set_pixel(i+2, 0, 0, 100)
		
	# 	ws.show()
	# 	time.sleep(0.05)
	#try:
	#	while True:
	#		pass
	#except KeyboardInterrupt:
	#	master.finish = True
			
