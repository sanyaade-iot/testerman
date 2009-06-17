# -*- coding: utf-8 -*-
##
# This file is part of Testerman, a test automation system.
# Copyright (c) 2009 QTesterman contributors
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
##

##
# A plugin to display logs as a simple text summary.
#
##

from PyQt4.Qt import *
from PyQt4.QtXml import *

from Base import *
from CommonWidgets import *

import Plugin
import PluginManager
import Documentation
import TemplateManagement

import os



##############################################################################
# Plugin Constants
##############################################################################

# Plugin ID, as generated by uuidgen
PLUGIN_ID = "86d2d0a0-266e-4735-bb25-54378c7b1a1d"
VERSION = "1.0.0"
DESCRIPTION = """
A template-based reporter that creates text-based reports.<br />
Templates are Velocity-compliant. The following objects/variables
are made available to them:
<ul>
<li>Global variables:</li>
<pre>
	record of Testcase testcases,
	integer ats_count,
	integer testcase_count,
	integer pass_count,
	integer fail_count,
	integer inconc_count,
	integer none_count,
	integer error_count,
	
	charstring pass_ratio, // percentage
	charstring fail_ratio, // percentage
	charstring inconc_ratio, // percentage
	charstring none_ratio, // percentage
	charstring error_ratio, // percentage
</pre>
<li>Types definition:</li>
<pre>
type record Testcase {
	Ats ats,
	charstring id,
	universal charstring title,
	charstring verdict,
	universal charstring doc, // the full testcase docstring
	universal charstring description, // the non-tagged docstring part
	TaggedDescription tag, // a record of @tag names available in the docstring
	record of Log userlogs,
	charstring duration,
	charstring start_time,
	charstring stop_time,
}

type record Ats {
	charstring id
	charstring duration,
	charstring start_time,
	charstring stop_time,
}

type record Log {
	charstring timestamp, // format HH:MM:SS.zzz, relative to start time
	universal charstring message,
}
</pre>
"""

DEFAULT_TEMPLATE_FILENAME = "templates/default-simple-report.txt"


##############################################################################
# Variables wrappers
##############################################################################

class AtsVariables:
	"""
	Behaves as a dict to access ats-related properties.
	
	Note: incomplete ATS models may be passed to this wrapper.
	As a consequence, some ats-related events may be missing
	when instanciating the object.
	
	That's why, in particular, stopTimestamp may not be 
	computed in some cases. 
	
	Allowing passing incomplete ATS models enables partial
	reporting of killed or running ATSes.
	"""
	def __init__(self, model):
		self._model = model
		# extract starttime/stoptime
		element = self._model.getDomElements("ats-started")[0]
		self._startTimestamp = float(element.attribute('timestamp'))
		
		try:
			element = self._model.getDomElements("ats-stopped")[0]
			self._stopTimestamp = float(element.attribute('timestamp'))
		except:
			self._stopTimestamp = None

	def __getitem__(self, name):
		if name == "id":
			return self._model.getId()
		elif name == "result":
			return self._model.getResult()
		elif name == "duration":
			if self._stopTimestamp is not None:
				return "%.2f" % (self._stopTimestamp - self._startTimestamp)
			else:
				return "N/A"
		else:
			raise KeyError(name)

class TestCaseVariables:
	"""
	Behaves as a dict to access testcase-related properties.
	
	Note: only complete testcases models are passed to
	this wrapper.
	As a consequence, it can safely assume that all mandatory
	events for a testcase are present (in particular start/stop events).
	"""
	def __init__(self, model):
		self._model = model
		self._atsVariables = AtsVariables(self._model.getAts())

		self._taggedDescription = Documentation.TaggedDocstring()
		self._taggedDescription.parse(self._model.getDescription())
		self._tags = Documentation.DictWrapper(self._taggedDescription)
		# extract starttime/stoptime

		element = self._model.getDomElements("testcase-started")[0]
		self._startTimestamp = float(element.attribute('timestamp'))
		
		element = self._model.getDomElements("testcase-stopped")[0]
		self._stopTimestamp = float(element.attribute('timestamp'))

	def __getitem__(self, name):
		if name == "ats":
			return self._atsVariables
		elif name == "id":
			return self._model.getId()
		elif name == "title":
			return self._model.getTitle()
		elif name == "verdict":
			return self._model.getVerdict()
		elif name == "doc":
			# Complete, raw description
			return self._taggedDescription.getString()
		elif name == "description":
			# The untagged part of the docstring
			return self._taggedDescription[''].value()
		elif name == "duration":
			return "%.2f" % (self._stopTimestamp - self._startTimestamp)
		elif name == "tag":
			return self._tags
		elif name == "userlogs":
			# Generated on the fly, as they may not be requested in all templates,
			# costly to compute, and requested only once per testcase if requested
			# (i.e. no cache required)
			ret = []
			for element in self._model.getDomElements("user"):
				timestamp = float(element.attribute('timestamp'))
				delta = timestamp - self._startTimestamp
				t = QTime().addMSecs(int(delta * 1000)).toString('hh:mm:ss.zzz')
				ret.append({'timestamp': t, 'message': element.text()})
			return ret
		else:
			raise KeyError(name)


##############################################################################
# Report View Plugin
##############################################################################

class MyTemplateApplicationWidget(TemplateManagement.WTemplateApplicationWidget):
	def __init__(self, parent = None):
		TemplateManagement.WTemplateApplicationWidget.__init__(self, PLUGIN_ID, DEFAULT_TEMPLATE_FILENAME, parent)

	def getTemplateManagementDialog(self):
		import Preferences
		dialog = Preferences.WPluginSettingsDialog("Manage templates", WPluginConfiguration, self)
		return dialog

class WReportView(Plugin.WReportView):
	def __init__(self, parent = None):
		Plugin.WReportView.__init__(self, parent)
		self.__createWidgets()

	##
	# Implementation specific
	##
	def __createWidgets(self):
		layout = QVBoxLayout()
		layout.setMargin(0)
		self._templateApplicationWidget = MyTemplateApplicationWidget()
		layout.addWidget(self._templateApplicationWidget)
		self.setLayout(layout)

	def _getSummaryVariables(self):
		"""
		Analyses the available log model, builds a list
		of dict usable for a summary template formatting.
		
		Provides the following template variables:
		
		for each testcase:
		ats-id, testcase-id, testcase-title, testcase-verdict
		
		summary/counts:
		ats-count, {pass,fail,inconc,none,error}-{count,ratio}
		"""
		count = 0
		counts = {}
		for s in ['pass', 'fail', 'inconc', 'none', 'error']:
			counts[s] = 0
		atsCount = 0

		for ats in self.getModel().getAtses():
			atsCount += 1
			for testcase in ats.getTestCases():
				if testcase.isComplete():
					v = testcase.getVerdict()
					if v in counts:
						counts[v] += 1
					count += 1

		summary = {
			'testcase_count': count,
			'ats_count': atsCount,
			'pass_count': counts['pass'],
			'fail_count': counts['fail'],
			'inconc_count': counts['inconc'],
			'none_count': counts['none'],
			'error_count': counts['error'],
			'pass_ratio': count and '%2.2f' % (float(counts['pass'])/float(count)*100.0) or '100',
			'fail_ratio': count and '%2.2f' % (float(counts['fail'])/float(count)*100.0) or '0',
			'inconc_ratio': count and '%2.2f' % (float(counts['inconc'])/float(count)*100.0) or '0',
			'none_ratio': count and '%2.2f' % (float(counts['none'])/float(count)*100.0) or '0',
			'error_ratio': count and '%2.2f' % (float(counts['error'])/float(count)*100.0) or '0',
		}
		return summary

	def _getTestCasesVariables(self):
		"""
		Returns a list of testcases variables
		"""
		ret = []
		for ats in self.getModel().getAtses():
			for testcase in ats.getTestCases():
				if testcase.isComplete():
					ret.append(TestCaseVariables(testcase))
		return ret

	##
	# Plugin.WReportView reimplementation
	##
	def displayLog(self):
		summary = self._getSummaryVariables()
		testcases = self._getTestCasesVariables()
		context = { 
			'summary': summary, 
			'testcases': testcases,
			'html_escape': TemplateManagement.html_escape
		}
		self._templateApplicationWidget.applyTemplate(context)

	def clearLog(self):
		self._templateApplicationWidget.clear()


##############################################################################
# Template Management (Plugin Configuration)
##############################################################################

class WPluginConfiguration(Plugin.WPluginConfiguration):
	def __init__(self, parent = None):
		Plugin.WPluginConfiguration.__init__(self, parent)
		self.__createWidgets()

	##
	# Plugin.WPluginConfiguration reimplementation
	##
	def displayConfiguration(self):
		templateModels = TemplateManagement.loadTemplates(PLUGIN_ID, DEFAULT_TEMPLATE_FILENAME)
		self._templateView.setModel(templateModels)

	def saveConfiguration(self):
		ret = TemplateManagement.saveTemplates(self._templateView.getModel(), PLUGIN_ID)
		return ret

	def checkConfiguration(self):
		return True

	##
	# Implementation specific
	##
	def __createWidgets(self):
		self.setMinimumWidth(350)
		layout = QVBoxLayout()

		self._templateView = TemplateManagement.WTemplateTreeView()
		layout.addWidget(self._templateView)

		self.setLayout(layout)



PluginManager.registerPluginClass("Simple Reporter", PLUGIN_ID, WReportView, WPluginConfiguration, version = VERSION, description = DESCRIPTION)

